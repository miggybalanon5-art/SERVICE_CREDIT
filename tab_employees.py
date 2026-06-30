"""
"Employees" tab: Dashboard, Encode Leave, Encode Credit, Manage Employees,
Leave Ledger, and Credit Ledger sub-tabs.

This is the largest tab by far, so it gets its own module. Rendered only
when the user has the "Employees" tab active (see app.py's routing), so none
of this code runs while someone is on a different tab.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from form6_actions import (
    _last_insert_id,
    available_credit,
    clean_required_text,
    clear_streamlit_cache,
    credit_balance_for,
    delete_credit_entry_by_id,
    delete_employee_cascade,
    delete_leave_entry_by_id,
    delete_reliever_entry_by_id,
    EMPLOYEE_DELETE_CONFIRM_KEY,
    ensure_connection,
    get_summary_safe,
    push_undo_action,
    render_undo_banner,
    update_employee_record,
)
from form6_data import MONTHS, month_from_date_value
from form6_attendance import build_attendance_summary
from form6_auth import (
    ACCOUNT_STATUS_APPROVED,
    ACCOUNT_STATUS_REJECTED,
    list_pending_accounts,
    load_users,
    set_account_status,
    set_employee_link,
)
from form6_cache import get_clean_state_frames
from form6_reliever import (
    load_reliever_summary,
    log_reliever_points_and_convert,
    points_to_hours,
    reliever_progress_for_employee,
)
from form6_settings import get_reliever_settings
from form6_store import (
    build_display_name,
    insert_leave_entry,
    insert_service_credit_entry,
    load_reliever_points,
    upsert_employee,
)
from form6_ui import (
    confirm_destructive_action,
    db_backup_bytes,
    employee_options,
    export_workbook_bytes,
    flash,
    keyed_tabs,
    queue_toast,
    readable_view,
    render_metrics,
    safe_text,
)


def _filter_employees_for_manage(employees: pd.DataFrame, search_term: str) -> pd.DataFrame:
    needle = search_term.casefold().strip()
    if not needle or employees.empty:
        return employees
    return employees[
        employees["employee_label"].astype(str).str.casefold().str.contains(needle, na=False)
        | employees["display_name"].astype(str).str.casefold().str.contains(needle, na=False)
        | employees["position"].fillna("").astype(str).str.casefold().str.contains(needle, na=False)
        | employees["grade"].astype(str).str.casefold().str.contains(needle, na=False)
    ]


def _on_manage_search_change() -> None:
    st.session_state["_sync_manage_to_filter"] = True
    st.rerun()


def _balance_cell(value: float) -> str:
    balance = float(value or 0)
    if balance < 0:
        return f":red[{balance:.1f}]"
    return f"{balance:.1f}"


def render(employees_all, leave_all, credit_all, employees_filtered, summary_filtered, leave_filtered, credit_filtered, attendance_all):
    emp_tabs = keyed_tabs(["Overview", "Encode Records", "Ledgers", "Attendance", "Manage"], "emp_nav_tab")

    # =========================================================================
    # DASHBOARD
    # =========================================================================
    with emp_tabs[0]:
        st.subheader("Overview")
        render_metrics(employees_filtered, leave_filtered)
        if not summary_filtered.empty:
            negative = summary_filtered[
                (summary_filtered["local_balance"] < 0) | (summary_filtered["national_balance"] < 0)
            ]
            if not negative.empty:
                st.warning(
                    f"{len(negative)} employee(s) have negative credit balance — review their ledgers for over-deductions or missing credits."
                )
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.subheader("Employee Summary")

        if summary_filtered.empty:
            st.info("No employees match the current filters.")
        else:
            h_cols = st.columns([0.10, 0.28, 0.18, 0.12, 0.12, 0.12, 0.08])
            h_cols[0].markdown("**Grade**")
            h_cols[1].markdown("**Employee**")
            h_cols[2].markdown("**Position**")
            h_cols[3].markdown("**Leave Days**")
            h_cols[4].markdown("**Local Bal**")
            h_cols[5].markdown("**Division Bal**")
            h_cols[6].markdown("**Action**")
            st.markdown('<div style="border-bottom: 2px solid var(--secondary-background-color); margin: 4px 0 12px 0;"></div>', unsafe_allow_html=True)

            for idx, row in summary_filtered.iterrows():
                emp_id, emp_label = int(row["id"]), str(row["employee_label"])
                r_cols = st.columns([0.10, 0.28, 0.18, 0.12, 0.12, 0.12, 0.08])
                r_cols[0].write(safe_text(row["grade"]))
                r_cols[1].markdown(f"<div style='text-align: left; font-weight: 600; color: var(--text-color);'>{emp_label}</div>", unsafe_allow_html=True)
                r_cols[2].write(safe_text(row["position"]) or "-")
                r_cols[3].write(f"{float(row['leave_days']):.1f}")
                r_cols[4].markdown(_balance_cell(row["local_balance"]))
                r_cols[5].markdown(_balance_cell(row["national_balance"]))

                with r_cols[6]:
                    with st.popover("+", help=f"Quick actions for {emp_label}"):
                        action_choice = st.selectbox("Select Action", ["Choose Action...", "Encode Leave", "Encode Service Credit"], key=f"qa_{emp_id}_{idx}")
                        if action_choice == "Encode Leave":
                            with st.form(key=f"q_l_{emp_id}_{idx}", clear_on_submit=True):
                                f_date = st.date_input("Date of filing", value=date.today())
                                st.caption(f"Month will be recorded as **{month_from_date_value(f_date) or '—'}** (from filing date).")
                                inc_dates = st.text_input("Inclusive dates")
                                c1, c2 = st.columns(2)
                                d_days = c1.number_input("Whole days", min_value=0.0, step=0.5, value=0.0)
                                h_days = c2.number_input("Half-day equivalent", min_value=0.0, step=0.5, value=0.0, help="Enter 0.5 for one half-day, 1.0 for two half-days, etc.")
                                l_type = st.selectbox("Leave type", ["PERSONAL LEAVE", "SICK LEAVE", "VACATION LEAVE", "SPECIAL PRIVILEGE LEAVE", "OTHER LEAVE"])
                                sc_avail = st.selectbox("Service credit availed", ["", "LOCAL", "DIVISION"])
                                if st.form_submit_button("Save Leave Record", use_container_width=True):
                                    req_d = float(d_days + h_days)
                                    is_valid = True
                                    if req_d <= 0:
                                        st.error("Enter at least 0.5 day (whole or half-day).")
                                        is_valid = False
                                    if is_valid and sc_avail in ["LOCAL", "DIVISION"]:
                                        actual_bal = float(row["local_balance"] if sc_avail == "LOCAL" else row["national_balance"])
                                        if available_credit(actual_bal) - req_d < 0:
                                            st.error(f"Insufficient {sc_avail} credit (available: {available_credit(actual_bal):.1f}).")
                                            is_valid = False
                                    if is_valid:
                                        try:
                                            with ensure_connection() as conn:
                                                insert_leave_entry(conn, emp_id, safe_text(f_date), inc_dates, "", d_days, h_days, l_type, sc_avail, "manual", "manual-entry")
                                                new_id = _last_insert_id(conn)
                                            if new_id:
                                                push_undo_action("leave", new_id, f"Leave for {emp_label}")
                                            clear_streamlit_cache()
                                            flash("Leave saved.")
                                            st.rerun()
                                        except ValueError as exc:
                                            st.error(str(exc))
                        elif action_choice == "Encode Service Credit":
                            with st.form(key=f"q_c_{emp_id}_{idx}", clear_on_submit=True):
                                ev_date = st.text_input("Inclusive Date Event")
                                cr_scope = st.selectbox("Credit scope", ["LOCAL", "DIVISION"])
                                srv_att = st.text_input("Service attended")
                                cr_units = st.number_input("Credit units", min_value=0.0, step=0.5, value=1.0)
                                if st.form_submit_button("Save Service Credit", use_container_width=True):
                                    with ensure_connection() as conn:
                                        insert_service_credit_entry(conn, emp_id, cr_scope, safe_text(ev_date), srv_att, cr_units, "manual", "manual-entry")
                                        new_id = _last_insert_id(conn)
                                    if new_id: push_undo_action("credit", new_id, f"Service credit for {emp_label}")
                                    clear_streamlit_cache()
                                    fresh_employees, fresh_leaves, fresh_credits, _ = get_clean_state_frames()
                                    new_balance = credit_balance_for(emp_id, cr_scope, fresh_employees, fresh_leaves, fresh_credits)
                                    queue_toast(f"{emp_label}: new {cr_scope.title()} balance is {new_balance:.1f}")
                                    flash("Service credit saved."); st.rerun()

            st.markdown('<div style="margin-bottom: 24px;"></div>', unsafe_allow_html=True)
            with st.expander("Show full breakdown (leave types & service credit detail)"):
                full_cols = ["grade", "employee_label", "position", "personal_leave", "sick_leave", "vacation_leave", "special_privilege_leave", "other_leave", "local_earned", "local_used", "local_balance", "national_earned", "national_used", "national_balance"]
                st.dataframe(readable_view(summary_filtered[full_cols]), use_container_width=True, hide_index=True)

        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.subheader("Export")
        st.caption("Generate a file below, then download it. Nothing is built until you click Generate.")
        export_cols = st.columns(4)

        with export_cols[0]:
            if st.button("Generate Excel backup", key="gen_excel_backup", use_container_width=True):
                export_tables = {
                    "Employee Summary": readable_view(summary_filtered), "Employees": readable_view(employees_filtered),
                    "Leave Entries": readable_view(leave_filtered), "Service Credits": readable_view(credit_filtered),
                }
                st.session_state["_excel_backup_bytes"] = export_workbook_bytes(export_tables)
            if "_excel_backup_bytes" in st.session_state:
                st.download_button("Download Excel backup", data=st.session_state["_excel_backup_bytes"], file_name="form6_tracker_backup.xlsx", mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", use_container_width=True)

        with export_cols[1]:
            if st.button("Generate database file", key="gen_db_backup", use_container_width=True):
                st.session_state["_db_backup_bytes"] = db_backup_bytes()
            if "_db_backup_bytes" in st.session_state:
                st.download_button("Download database file", data=st.session_state["_db_backup_bytes"], file_name="form6_tracker.sqlite3", mime="application/octet-stream", use_container_width=True)

        export_cols[2].download_button("Leave CSV", data=readable_view(leave_filtered).to_csv(index=False).encode("utf-8"), file_name="leave_entries.csv", mime="text/csv", use_container_width=True)
        export_cols[3].download_button("Summary CSV", data=readable_view(summary_filtered).to_csv(index=False).encode("utf-8"), file_name="employee_summary.csv", mime="text/csv", use_container_width=True)

    # =========================================================================
    # ENCODE RECORDS (leave + service credit in sub-tabs)
    # =========================================================================
    with emp_tabs[1]:
        st.subheader("Encode Records")
        employee_map = employee_options(employees_all)
        if not employee_map:
            st.info("Add an employee first.")
        else:
            summary_all = get_summary_safe(employees_all, leave_all, credit_all)
            encode_tabs = keyed_tabs(["Leave", "Service Credit", "Reliever Points"], "encode_nav_tab")

            with encode_tabs[0]:
                with st.form("leave_form", clear_on_submit=True):
                    employee_label = st.selectbox("Employee", list(employee_map.keys()), key="leave_form_employee")
                    filing_date = st.date_input("Date of filing", value=date.today(), key="leave_form_date")
                    st.caption(f"Month: **{month_from_date_value(filing_date) or '—'}** (auto from filing date)")
                    inclusive_dates = st.text_input("Inclusive dates", key="leave_form_inclusive")
                    c_c, c_d = st.columns(2)
                    no_days = c_c.number_input("Whole days", min_value=0.0, step=0.5, value=0.0, key="leave_form_whole")
                    no_halfdays = c_d.number_input("Half-day equivalent", min_value=0.0, step=0.5, value=0.0, key="leave_form_half", help="0.5 = one half-day")
                    leave_type = st.selectbox("Leave type", ["PERSONAL LEAVE", "SICK LEAVE", "VACATION LEAVE", "SPECIAL PRIVILEGE LEAVE", "OTHER LEAVE"], index=0, key="leave_form_type")
                    service_credit_availed = st.selectbox("Service credit availed", ["", "LOCAL", "DIVISION"], index=0, key="leave_form_sc")
                    if st.form_submit_button("Save leave record", use_container_width=True):
                        employee_id = employee_map[employee_label]
                        req_days = float(no_days + no_halfdays)
                        is_valid = True
                        if req_days <= 0:
                            st.error("Enter at least 0.5 day (whole or half-day).")
                            is_valid = False
                        if is_valid and service_credit_availed in ["LOCAL", "DIVISION"]:
                            emp_summary = summary_all[summary_all["employee_label"] == employee_label]
                            bal_col = "local_balance" if service_credit_availed == "LOCAL" else "national_balance"
                            curr_bal = float(emp_summary[bal_col].fillna(0).values[0]) if not emp_summary.empty else 0.0
                            if available_credit(curr_bal) - req_days < 0:
                                st.error(f"Insufficient balance (available: {available_credit(curr_bal):.1f}).")
                                is_valid = False
                        if is_valid:
                            try:
                                with ensure_connection() as conn:
                                    insert_leave_entry(
                                        conn, employee_id=employee_id, date_of_filing=safe_text(filing_date),
                                        inclusive_dates=inclusive_dates, month="", no_days=no_days, no_halfdays=no_halfdays,
                                        leave_type=leave_type, service_credit_availed=service_credit_availed,
                                        source_kind="manual", source_ref="manual-entry",
                                    )
                                    new_id = _last_insert_id(conn)
                                if new_id:
                                    push_undo_action("leave", new_id, f"Leave record for {employee_label}")
                                clear_streamlit_cache()
                                flash("Leave record saved.")
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))
                render_undo_banner("leave_tab")

            with encode_tabs[1]:
                st.caption("Encode service credit for one or more employees at once.")
                selected_employee_labels = st.multiselect("Select employees", list(employee_map.keys()), key="credit_employees")
                with st.form("credit_form", clear_on_submit=True):
                    event_date = st.text_input("Event date", key="credit_form_date")
                    credit_scope = st.selectbox("Credit scope", ["LOCAL", "DIVISION"], index=0, key="credit_form_scope")
                    service_attended = st.text_input("Service attended", key="credit_form_service")
                    credit_units = st.number_input("Credit units", min_value=0.0, step=0.5, value=1.0, key="credit_form_units")
                    if st.form_submit_button("Save service credit", use_container_width=True):
                        if not selected_employee_labels:
                            st.error("Please select at least one employee.")
                        else:
                            try:
                                inserted_ids = []
                                with ensure_connection() as conn:
                                    for label in selected_employee_labels:
                                        employee_id = employee_map[label]
                                        insert_service_credit_entry(
                                            conn, employee_id=employee_id, credit_scope=credit_scope,
                                            event_date=safe_text(event_date), service_attended=service_attended,
                                            credit_units=credit_units, source_kind="manual", source_ref="manual-entry",
                                        )
                                        if row_id := _last_insert_id(conn):
                                            inserted_ids.append(row_id)
                                if inserted_ids:
                                    push_undo_action("credit", inserted_ids[-1], "Service credit batch")
                                clear_streamlit_cache()
                                fresh_employees, fresh_leaves, fresh_credits, _ = get_clean_state_frames()
                                if len(selected_employee_labels) == 1:
                                    label = selected_employee_labels[0]
                                    new_balance = credit_balance_for(employee_map[label], credit_scope, fresh_employees, fresh_leaves, fresh_credits)
                                    queue_toast(f"{label}: new {credit_scope.title()} balance is {new_balance:.1f}")
                                else:
                                    queue_toast(f"Service credit saved for {len(selected_employee_labels)} employees.")
                                flash("Service credit records saved.")
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))
                render_undo_banner("credit_tab")

            with encode_tabs[2]:
                reliever_settings = get_reliever_settings()
                minutes_per_point = reliever_settings["minutes_per_point"]
                points_per_credit = reliever_settings["points_per_credit"]
                hours_per_credit = points_to_hours(points_per_credit, minutes_per_point)
                st.caption(
                    f"1 point = {minutes_per_point:.0f} minutes of relieving duty. "
                    f"Every {points_per_credit:.0f} points ({hours_per_credit:.1f} hours) earns 1 LOCAL "
                    f"service credit automatically. Leftover points carry over toward the next credit — "
                    f"nothing is lost."
                )
                reliever_employee_label = st.selectbox(
                    "Employee", list(employee_map.keys()), key="reliever_form_employee"
                )
                reliever_employee_id = employee_map[reliever_employee_label]

                # Show this employee's current standing before they log a new
                # session, so it's clear how close they are to the next credit.
                progress_before = reliever_progress_for_employee(reliever_employee_id)
                p_cols = st.columns(3)
                p_cols[0].metric("Total Points Logged", f"{progress_before['total_points']:.2f}")
                p_cols[1].metric("Credits Earned So Far", int(progress_before["credits_issued"]))
                p_cols[2].metric(
                    "Points Toward Next Credit",
                    f"{progress_before['points_carried_over']:.2f} / {points_per_credit:.0f}",
                )

                with st.form("reliever_form", clear_on_submit=True):
                    reliever_date = st.date_input("Date of duty", value=date.today(), key="reliever_form_date")
                    reliever_points = st.number_input(
                        "Points for this session",
                        min_value=0.0, step=1.0, value=1.0,
                        key="reliever_form_points",
                        help=f"1 point = {minutes_per_point:.0f} minutes. E.g. a 6-hour reliever session = {points_per_credit:.0f} points.",
                    )
                    reliever_notes = st.text_input(
                        "Notes (optional)", key="reliever_form_notes",
                        placeholder="e.g. Relieved for Teacher X, Grade 7 Math",
                    )
                    if st.form_submit_button("Save reliever points", use_container_width=True):
                        if reliever_points <= 0:
                            st.error("Enter at least 1 point.")
                        else:
                            try:
                                with ensure_connection() as conn:
                                    result = log_reliever_points_and_convert(
                                        conn,
                                        employee_id=reliever_employee_id,
                                        points=reliever_points,
                                        entry_date=safe_text(reliever_date),
                                        notes=reliever_notes,
                                        source_kind="manual",
                                        source_ref="manual-entry",
                                    )
                                clear_streamlit_cache()
                                if result["credits_newly_issued"] > 0:
                                    queue_toast(
                                        f"{reliever_employee_label}: earned "
                                        f"{result['credits_newly_issued']} new LOCAL service credit"
                                        f"{'s' if result['credits_newly_issued'] != 1 else ''} from reliever points!"
                                    )
                                    flash(
                                        f"Reliever points saved — {result['credits_newly_issued']} new LOCAL "
                                        f"credit{'s' if result['credits_newly_issued'] != 1 else ''} issued."
                                    )
                                else:
                                    queue_toast(
                                        f"{reliever_employee_label}: {result['points_carried_over']:.2f} of "
                                        f"{points_per_credit:.0f} points toward the next credit."
                                    )
                                    flash("Reliever points saved.")
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))

    # =========================================================================
    # LEDGERS
    # =========================================================================
    with emp_tabs[2]:
        ledger_tabs = keyed_tabs(["Leave", "Deductions", "Service Credits", "Reliever Points"], "ledger_nav_tab")

        with ledger_tabs[0]:
            st.subheader("Leave Ledger (By Employee)")
            leave_only = leave_filtered[leave_filtered.get("source_kind", "") != "biometrics"]
            if leave_only.empty:
                st.info("No leave records match the current filters.")
            else:
                st.caption(f"{len(leave_only):,} leave row(s) across {leave_only['employee_label'].nunique():,} employee(s).")
                for emp_label in sorted(leave_only["employee_label"].dropna().unique()):
                    emp_data = leave_only[leave_only["employee_label"] == emp_label]
                    with st.expander(f"{emp_label} - {len(emp_data)} leave record(s)"):
                        h_cols = st.columns([0.12, 0.10, 0.15, 0.10, 0.18, 0.12, 0.12])
                        h_cols[0].markdown("**Filing Date**")
                        h_cols[1].markdown("**Month**")
                        h_cols[2].markdown("**Leave Type**")
                        h_cols[3].markdown("**Days**")
                        h_cols[4].markdown("**Inclusive Dates**")
                        h_cols[5].markdown("**Credit Used**")
                        h_cols[6].markdown("**Action**")
                        st.divider()
                        for _, leave_row in emp_data.iterrows():
                            leave_id = int(leave_row["id"])
                            r_cols = st.columns([0.12, 0.10, 0.15, 0.10, 0.18, 0.12, 0.12])
                            r_cols[0].write(safe_text(leave_row.get("date_of_filing")))
                            r_cols[1].write(leave_row.get("month", ""))
                            r_cols[2].write(leave_row.get("leave_type", ""))
                            r_cols[3].write(f"{leave_row.get('total_days', 0):.1f}")
                            r_cols[4].write(leave_row.get("inclusive_dates", "") or "-")
                            r_cols[5].write(leave_row.get("service_credit_availed", "") or "-")
                            with r_cols[6]:
                                if st.button("Delete", key=f"leave_delete_{leave_id}", help="Remove this leave record", use_container_width=True):
                                    if confirm_destructive_action(f"leave_{leave_id}", f"leave record for {emp_label}", "delete"):
                                        delete_leave_entry_by_id(leave_id)
                                        clear_streamlit_cache()
                                        flash(f"Removed leave record for {emp_label}.")
                                        st.rerun()

        with ledger_tabs[1]:
            st.subheader("Deductions Ledger (Lates & Absents)")
            st.caption("Credit deductions from biometrics. These reduce balance like availed leave, but are not leave the employee filed.")
            deductions_only = leave_filtered[leave_filtered.get("source_kind", "") == "biometrics"]
            if deductions_only.empty:
                st.info("No biometrics deductions match the current filters.")
            else:
                st.caption(f"{len(deductions_only):,} deduction row(s) across {deductions_only['employee_label'].nunique():,} employee(s).")
                for emp_label in sorted(deductions_only["employee_label"].dropna().unique()):
                    emp_data = deductions_only[deductions_only["employee_label"] == emp_label]
                    with st.expander(f"{emp_label} - {len(emp_data)} deduction(s)"):
                        h_cols = st.columns([0.15, 0.15, 0.15, 0.15, 0.25, 0.15])
                        h_cols[0].markdown("**Date**")
                        h_cols[1].markdown("**Type**")
                        h_cols[2].markdown("**Days Deducted**")
                        h_cols[3].markdown("**Scope**")
                        h_cols[4].markdown("**Source File**")
                        h_cols[5].markdown("**Action**")
                        st.divider()
                        for _, ded_row in emp_data.iterrows():
                            ded_id = int(ded_row["id"])
                            r_cols = st.columns([0.15, 0.15, 0.15, 0.15, 0.25, 0.15])
                            r_cols[0].write(safe_text(ded_row.get("date_of_filing")))
                            r_cols[1].write(ded_row.get("leave_type", ""))
                            r_cols[2].write(f"-{ded_row.get('total_days', 0):.2f}")
                            r_cols[3].write(ded_row.get("service_credit_availed", "") or "-")
                            r_cols[4].write(ded_row.get("source_ref", "") or "-")
                            with r_cols[5]:
                                if st.button("Delete", key=f"deduction_delete_{ded_id}", help="Remove this deduction", use_container_width=True):
                                    if confirm_destructive_action(f"deduction_{ded_id}", f"deduction for {emp_label}", "delete"):
                                        delete_leave_entry_by_id(ded_id)
                                        clear_streamlit_cache()
                                        flash(f"Removed deduction for {emp_label}.")
                                        st.rerun()

        with ledger_tabs[2]:
            st.subheader("Service Credit Ledger (By Employee)")
            if credit_filtered.empty:
                st.info("No service credit records match the current filters.")
            else:
                st.caption(f"{len(credit_filtered):,} credit row(s) across {credit_filtered['employee_label'].nunique():,} employee(s).")
                for emp_label in sorted(credit_filtered["employee_label"].dropna().unique()):
                    emp_data = credit_filtered[credit_filtered["employee_label"] == emp_label]
                    with st.expander(f"{emp_label} - {len(emp_data)} service credit record(s)"):
                        h_cols = st.columns([0.15, 0.20, 0.35, 0.15, 0.15])
                        h_cols[0].markdown("**Scope**")
                        h_cols[1].markdown("**Event Date**")
                        h_cols[2].markdown("**Service Attended**")
                        h_cols[3].markdown("**Units**")
                        h_cols[4].markdown("**Action**")
                        st.divider()
                        for _, credit_row in emp_data.iterrows():
                            credit_id = int(credit_row["id"])
                            r_cols = st.columns([0.15, 0.20, 0.35, 0.15, 0.15])
                            r_cols[0].write(credit_row.get("credit_scope", ""))
                            r_cols[1].write(safe_text(credit_row.get("event_date")))
                            r_cols[2].write(credit_row.get("service_attended", ""))
                            r_cols[3].write(f"{credit_row.get('credit_units', 0):.1f}")
                            with r_cols[4]:
                                if st.button("Delete", key=f"credit_delete_{credit_id}", help="Remove this service credit record", use_container_width=True):
                                    if confirm_destructive_action(f"credit_{credit_id}", f"service credit for {emp_label}", "delete"):
                                        delete_credit_entry_by_id(credit_id)
                                        clear_streamlit_cache()
                                        flash(f"Removed service credit record for {emp_label}.")
                                        st.rerun()

        with ledger_tabs[3]:
            st.subheader("Reliever Points Ledger (By Employee)")
            reliever_settings = get_reliever_settings()
            points_per_credit = reliever_settings["points_per_credit"]
            st.caption(
                f"Every {points_per_credit:.0f} points of relieving duty automatically earns 1 LOCAL "
                f"service credit (see the Service Credits tab for those — they're tagged as earned "
                f"from \"Reliever duty\"). Leftover points below {points_per_credit:.0f} carry over."
            )
            reliever_all = load_reliever_points()
            if reliever_all.empty:
                st.info("No reliever points have been logged yet.")
            else:
                # Filter by the same employee set as the rest of this tab,
                # so switching the sidebar's grade/employee filters also
                # narrows this ledger consistently with every other one.
                allowed_labels = set(employees_filtered["employee_label"]) if not employees_filtered.empty else set()
                reliever_filtered = reliever_all[reliever_all["employee_label"].isin(allowed_labels)] if allowed_labels else reliever_all

                if reliever_filtered.empty:
                    st.info("No reliever points match the current filters.")
                else:
                    st.caption(
                        f"{len(reliever_filtered):,} session(s) across "
                        f"{reliever_filtered['employee_label'].nunique():,} employee(s)."
                    )
                    for emp_label in sorted(reliever_filtered["employee_label"].dropna().unique()):
                        emp_data = reliever_filtered[reliever_filtered["employee_label"] == emp_label]
                        emp_id_for_label = int(emp_data.iloc[0]["employee_id"])
                        progress = reliever_progress_for_employee(emp_id_for_label)
                        header = (
                            f"{emp_label} — {len(emp_data)} session(s), "
                            f"{progress['total_points']:.2f} pts total, "
                            f"{int(progress['credits_issued'])} credit(s) earned"
                        )
                        with st.expander(header):
                            h_cols = st.columns([0.15, 0.15, 0.45, 0.25])
                            h_cols[0].markdown("**Date**")
                            h_cols[1].markdown("**Points**")
                            h_cols[2].markdown("**Notes**")
                            h_cols[3].markdown("**Action**")
                            st.divider()
                            for _, pt_row in emp_data.sort_values("entry_date", ascending=False, na_position="last").iterrows():
                                pt_id = int(pt_row["id"])
                                r_cols = st.columns([0.15, 0.15, 0.45, 0.25])
                                r_cols[0].write(safe_text(pt_row.get("entry_date")))
                                r_cols[1].write(f"{float(pt_row.get('points', 0) or 0):.2f}")
                                r_cols[2].write(pt_row.get("notes", "") or "-")
                                with r_cols[3]:
                                    if st.button(
                                        "Delete", key=f"reliever_delete_{pt_id}",
                                        help="Remove this reliever points session. Does NOT retract any credit already issued from it.",
                                        use_container_width=True,
                                    ):
                                        if confirm_destructive_action(f"reliever_{pt_id}", f"reliever points session for {emp_label}", "delete"):
                                            delete_reliever_entry_by_id(pt_id)
                                            clear_streamlit_cache()
                                            flash(
                                                f"Removed reliever points session for {emp_label}. "
                                                f"Note: any LOCAL credit already issued from past points was not removed."
                                            )
                                            st.rerun()

    # =========================================================================
    # ATTENDANCE SUMMARY
    # =========================================================================
    with emp_tabs[3]:
        st.subheader("Attendance Summary")
        st.caption(
            "Per-employee monthly counts from imported biometrics data. \"On Leave\" days are taken from "
            "approved leave records, not from the biometrics file - so a day marked ABSENT by the device "
            "because the employee was actually on leave still counts as On Leave here, not Absent."
        )

        if attendance_all.empty:
            st.info("No biometrics data has been imported yet. Use Import / Backup to upload a month's biometrics file.")
        else:
            available_dates = pd.to_datetime(attendance_all["record_date"], errors="coerce").dropna()
            available_months = sorted({(d.year, d.month) for d in available_dates}, reverse=True)

            month_labels = {f"{MONTHS[m-1].title()} {y}": (y, m) for y, m in available_months}

            selected_label = st.selectbox("Month", list(month_labels.keys()), index=0, key="attendance_summary_month")
            selected_year, selected_month = month_labels[selected_label]

            summary = build_attendance_summary(attendance_all, leave_all, employees_filtered, selected_year, selected_month)

            if summary.empty:
                st.info("No employees match the current filters.")
            else:
                summary = summary.sort_values("employee_label")
                h_cols = st.columns([0.10, 0.32, 0.14, 0.14, 0.14, 0.16])
                h_cols[0].markdown("**Grade**")
                h_cols[1].markdown("**Employee**")
                h_cols[2].markdown("**Present**")
                h_cols[3].markdown("**Late**")
                h_cols[4].markdown("**Absent**")
                h_cols[5].markdown("**On Leave**")
                st.markdown('<div style="border-bottom: 2px solid var(--secondary-background-color); margin: 4px 0 12px 0;"></div>', unsafe_allow_html=True)

                for _, row in summary.iterrows():
                    r_cols = st.columns([0.10, 0.32, 0.14, 0.14, 0.14, 0.16])
                    r_cols[0].write(row["grade"])
                    r_cols[1].markdown(f"<div style='text-align: left; font-weight: 600; color: var(--text-color);'>{row['employee_label']}</div>", unsafe_allow_html=True)
                    r_cols[2].write(int(row["present_days"]))
                    r_cols[3].write(int(row["late_days"]))
                    r_cols[4].write(int(row["absent_days"]))
                    r_cols[5].write(int(row["on_leave_days"]))

                st.markdown('<div style="margin-bottom: 12px;"></div>', unsafe_allow_html=True)
                summary_export = summary.rename(columns={
                    "employee_label": "Employee", "grade": "Grade", "present_days": "Present",
                    "late_days": "Late", "absent_days": "Absent", "on_leave_days": "On Leave",
                    "total_recorded_days": "Total Days",
                })[["Grade", "Employee", "Present", "Late", "Absent", "On Leave", "Total Days"]]
                st.download_button(
                    f"Download {selected_label} attendance summary (CSV)",
                    data=summary_export.to_csv(index=False).encode("utf-8"),
                    file_name=f"attendance_summary_{selected_year}_{selected_month:02d}.csv",
                    mime="text/csv",
                    use_container_width=True,
                )

    # =========================================================================
    # MANAGE EMPLOYEES
    # =========================================================================
    with emp_tabs[4]:
        st.subheader("Manage Employees")
        st.text_input(
            "Quick search employees",
            placeholder="Search by name, grade, or position...",
            key="manage_search_term",
            help="Synced with the sidebar search — applies across all tabs.",
            on_change=_on_manage_search_change,
        )
        manage_employees = _filter_employees_for_manage(
            employees_filtered,
            st.session_state.get("manage_search_term", ""),
        )
        with st.expander("Add or update employee", expanded=not bool(len(employees_all))):
            with st.form("employee_form", clear_on_submit=True):
                c_a, c_b = st.columns(2)
                with c_a:
                    grade = st.text_input("Grade", value="G7")
                with c_b:
                    position = st.text_input("Position")
                c_c, c_d, c_e = st.columns([1, 1, 0.4])
                with c_c:
                    last_name = st.text_input("Last name")
                with c_d:
                    first_name = st.text_input("First name")
                with c_e:
                    middle_initial = st.text_input("M.I.", max_chars=5)
                if st.form_submit_button("Save employee", use_container_width=True):
                    display_name = build_display_name(last_name, first_name, middle_initial)
                    if not clean_required_text(grade) or not clean_required_text(display_name):
                        st.error("Grade and name are required.")
                    else:
                        with ensure_connection() as conn:
                            upsert_employee(conn, grade=grade, display_name=display_name, position=position, source_kind="manual", source_ref="manual-entry")
                        clear_streamlit_cache()
                        flash("Employee saved.")
                        st.rerun()
        if not employees_all.empty and manage_employees.empty:
            st.info("No employees match your search.")
        elif not manage_employees.empty:
            st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
            for _, emp_row in manage_employees.sort_values("display_name").iterrows():
                emp_id = int(emp_row["id"])
                if st.session_state.get("EDIT_EMPLOYEE_ID") == emp_id:
                    with st.form(key=f"edit_employee_form_{emp_id}"):
                        e_cols = st.columns([0.18, 0.42, 0.25])
                        with e_cols[0]:
                            edit_grade = st.text_input("Grade", value=safe_text(emp_row.get("grade")), key=f"eg_{emp_id}")
                        with e_cols[1]:
                            edit_name = st.text_input("Name", value=safe_text(emp_row.get("display_name")), key=f"en_{emp_id}")
                        with e_cols[2]:
                            edit_position = st.text_input("Position", value=safe_text(emp_row.get("position")), key=f"ep_{emp_id}")
                        save_col, cancel_col = st.columns(2)
                        save_clicked = save_col.form_submit_button("Save changes", use_container_width=True)
                        cancel_clicked = cancel_col.form_submit_button("Cancel", use_container_width=True)
                    if save_clicked:
                        if not clean_required_text(edit_grade) or not clean_required_text(edit_name):
                            st.error("Grade and name are required.")
                        else:
                            try:
                                update_employee_record(emp_id, edit_grade, edit_position, edit_name)
                                st.session_state["EDIT_EMPLOYEE_ID"] = None
                                clear_streamlit_cache()
                                flash("Employee updated.")
                                st.rerun()
                            except ValueError as exc:
                                st.error(str(exc))
                    if cancel_clicked:
                        st.session_state["EDIT_EMPLOYEE_ID"] = None
                        st.rerun()
                else:
                    row_cols = st.columns([0.13, 0.32, 0.20, 0.15, 0.20])
                    row_cols[0].write(emp_row.get("grade", ""))
                    row_cols[1].write(emp_row.get("display_name", ""))
                    row_cols[2].write(emp_row.get("position", "") or "-")
                    if row_cols[3].button("Edit", key=f"e_req_{emp_id}", use_container_width=True):
                        st.session_state["EDIT_EMPLOYEE_ID"] = emp_id
                        st.rerun()
                    if st.session_state.get(EMPLOYEE_DELETE_CONFIRM_KEY) == emp_id:
                        if row_cols[4].button("Confirm", key=f"d_y_{emp_id}", use_container_width=True):
                            delete_employee_cascade(emp_id)
                            st.session_state[EMPLOYEE_DELETE_CONFIRM_KEY] = None
                            clear_streamlit_cache()
                            st.rerun()
                        if row_cols[4].button("Cancel", key=f"d_n_{emp_id}", use_container_width=True):
                            st.session_state[EMPLOYEE_DELETE_CONFIRM_KEY] = None
                            st.rerun()
                    else:
                        with row_cols[4]:
                            if st.button("Delete", key=f"d_req_{emp_id}", use_container_width=True):
                                if confirm_destructive_action(f"employee_{emp_id}", emp_row.get("display_name", ""), "delete"):
                                    delete_employee_cascade(emp_id)
                                    st.session_state[EMPLOYEE_DELETE_CONFIRM_KEY] = None
                                    clear_streamlit_cache()
                                    flash(f"Deleted employee {emp_row.get('display_name', '')}.")
                                    st.rerun()

        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.subheader("Account Approvals")
        st.caption(
            "Employee Portal accounts pick their own employee record when they self-register, with no "
            "verification that the person signing up is actually that employee. Review and approve each "
            "request below before that account can see any real data."
        )
        pending_accounts = list_pending_accounts()
        if not pending_accounts:
            st.success("No accounts are waiting for approval.")
        else:
            employee_label_by_id_for_approval = dict(zip(employees_all["id"], employees_all["employee_label"])) if not employees_all.empty else {}
            for pending in pending_accounts:
                pending_uname = pending["username"]
                pending_eid = pending["employee_id"]
                claimed_label = employee_label_by_id_for_approval.get(pending_eid, "(unknown employee record)")
                p_cols = st.columns([0.5, 0.25, 0.25])
                p_cols[0].markdown(f"**{pending_uname}** is requesting access as: **{claimed_label}**")
                if p_cols[1].button("Approve", key=f"approve_{pending_uname}", use_container_width=True, type="primary"):
                    set_account_status(pending_uname, ACCOUNT_STATUS_APPROVED)
                    from form6_auth import log_action
                    log_action(st.session_state.current_user, "ACCOUNT_APPROVED", f"{pending_uname} approved as {claimed_label} (employee_id={pending_eid})")
                    clear_streamlit_cache()
                    flash(f"Approved '{pending_uname}'.")
                    st.rerun()
                if p_cols[2].button("Reject", key=f"reject_{pending_uname}", use_container_width=True):
                    set_account_status(pending_uname, ACCOUNT_STATUS_REJECTED)
                    from form6_auth import log_action
                    log_action(st.session_state.current_user, "ACCOUNT_REJECTED", f"{pending_uname} rejected (had claimed {claimed_label}, employee_id={pending_eid})")
                    clear_streamlit_cache()
                    flash(f"Rejected '{pending_uname}'. They can be re-linked manually below if this was a mistake.")
                    st.rerun()

        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.subheader("Employee Portal Accounts")
        st.caption("Control which login account can view which employee's own records.")
        employee_label_by_id = dict(zip(employees_all["id"], employees_all["employee_label"])) if not employees_all.empty else {}
        link_choices = {"— No employee link —": None}
        link_choices.update({label: eid for eid, label in employee_label_by_id.items()})
        link_labels = list(link_choices.keys())

        portal_users = {u: d for u, d in load_users().items() if d.get("role") != "admin"}
        if not portal_users:
            st.info("No staff or employee accounts yet. Create one from the login screen's 'Create Account' tab.")
        else:
            for uname, udata in sorted(portal_users.items()):
                current_link_label = employee_label_by_id.get(udata.get("employee_id"), "— No employee link —")
                header = f"{uname} — {'Employee Portal' if udata.get('role') == 'employee' else 'Staff/Admin'}"
                if udata.get("role") == "employee" and udata.get("employee_id") in employee_label_by_id:
                    header += f" ({employee_label_by_id[udata['employee_id']]})"
                with st.expander(header):
                    col1, col2, col3 = st.columns([0.32, 0.48, 0.20])
                    new_role_choice = col1.selectbox(
                        "Account type", ["Staff / Admin", "Employee Portal"],
                        index=(1 if udata.get("role") == "employee" else 0),
                        key=f"role_choice_{uname}",
                    )
                    default_idx = link_labels.index(current_link_label) if current_link_label in link_labels else 0
                    new_link_label = col2.selectbox("Linked employee", link_labels, index=default_idx, key=f"link_choice_{uname}")
                    if col3.button("Save", key=f"save_link_{uname}", use_container_width=True):
                        new_role = "employee" if new_role_choice == "Employee Portal" else "user"
                        new_employee_id = link_choices[new_link_label]
                        if new_role == "employee" and new_employee_id is None:
                            st.error("Select an employee to link before saving an Employee Portal account.")
                        else:
                            set_employee_link(uname, new_employee_id if new_role == "employee" else None, role=new_role)
                            log_action_user = st.session_state.current_user
                            from form6_auth import log_action
                            log_action(log_action_user, "PORTAL_LINK_UPDATED", f"{uname} -> role={new_role}, employee_id={new_employee_id}")
                            clear_streamlit_cache()
                            flash(f"Updated account '{uname}'.")
                            st.rerun()
