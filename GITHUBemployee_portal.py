"""
Employee self-service portal: a read-only view scoped to a single employee's
own leave and service credit records, laid out as an Overview (Local/Division
balance), a color-coded monthly attendance calendar, plus a Leave Ledger,
Deduction Ledger, and a Credit Ledger.
"""

from __future__ import annotations

import calendar as calendar_module
from datetime import date
import os
import pandas as pd

import streamlit as st

from form6_actions import build_credit_ledger, get_summary_safe
from form6_attendance import build_attendance_calendar_days
from form6_auth import (
    ACCOUNT_STATUS_PENDING,
    ACCOUNT_STATUS_REJECTED,
    BASE_DIR,
    LOGO_PATH,
    normalize_username,
    perform_logout,
)
from form6_cache import get_attendance_frame, get_clean_state_frames
from form6_reliever import points_to_hours, reliever_progress_for_employee
from form6_store import ensure_database, load_reliever_points
from form6_ui import (
        export_workbook_bytes,
        inject_app_css,
        readable_view,
        safe_text,
        show_flash,
)


# Color coding for the attendance calendar. Kept as a module-level constant
# (rather than buried in the render function) so it's easy to find and tweak
# in one place if the school wants different colors later.
ATTENDANCE_STATUS_COLORS = {
    "PRESENT": "#22C55E",   # green
    "LATE": "#F59E0B",      # amber
    "ABSENT": "#EF4444",    # red
    "ON_LEAVE": "#3B82F6",  # blue
}
ATTENDANCE_STATUS_LABELS = {
    "PRESENT": "Present",
    "LATE": "Late",
    "ABSENT": "Absent",
    "ON_LEAVE": "On Leave",
}


def _render_attendance_calendar_html(year: int, month: int, day_status: dict) -> str:
    """Builds a compact, Google-Calendar-style month grid as one HTML
    string: 7 columns (Sun-Sat), fixed-height rows (not square cells - a
    square cell at full column width gets huge), with leading blank cells
    so day 1 lines up under its actual weekday. Each day with a known
    status is tinted with that status's color, similar to how Google
    Calendar fills a day with an event's color. Hovering a day shows its
    status as a native tooltip."""
    days_in_month = calendar_module.monthrange(year, month)[1]
    first_weekday = calendar_module.monthrange(year, month)[0]  # 0=Monday
    leading_blanks = (first_weekday + 1) % 7  # convert to Sunday-first grid
    today = date.today()

    weekday_headers = "".join(
        f'<div class="cal-weekday">{wd}</div>' for wd in ["S", "M", "T", "W", "T", "F", "S"]
    )

    cells = []
    for _ in range(leading_blanks):
        cells.append('<div class="cal-cell cal-blank"></div>')

    for day_num in range(1, days_in_month + 1):
        this_date = date(year, month, day_num)
        status = day_status.get(this_date)
        is_today = (this_date == today)
        today_class = " cal-today" if is_today else ""

        if status and status in ATTENDANCE_STATUS_COLORS:
            color = ATTENDANCE_STATUS_COLORS[status]
            label = ATTENDANCE_STATUS_LABELS[status]
            cells.append(
                f'<div class="cal-cell{today_class}" style="background:{color}3D;" title="{label}">'
                f'<span class="cal-daynum" style="color:{color};">{day_num}</span>'
                f'</div>'
            )
        else:
            cells.append(
                f'<div class="cal-cell{today_class}">'
                f'<span class="cal-daynum">{day_num}</span>'
                f'</div>'
            )

    grid_css = """
    <style>
    .cal-grid-wrapper { margin: 4px 0 2px 0; max-width: 320px; }
    .cal-weekday-row { display: grid; grid-template-columns: repeat(7, 1fr); margin-bottom: 2px; }
    .cal-weekday { text-align: center; font-size: 10px; font-weight: 600; opacity: 0.55; }
    .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
    .cal-cell {
        height: 30px;
        border-radius: 4px;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .cal-cell.cal-blank { background: transparent; }
    .cal-cell.cal-today { box-shadow: inset 0 0 0 1.5px var(--primary-color, #0064E0); }
    .cal-daynum { font-size: 12px; font-weight: 500; }
    </style>
    """

    return (
        grid_css
        + '<div class="cal-grid-wrapper">'
        + f'<div class="cal-weekday-row">{weekday_headers}</div>'
        + f'<div class="cal-grid">{"".join(cells)}</div>'
        + '</div>'
    )


def _render_attendance_legend() -> None:
    legend_items = "".join(
        f'<span style="display:inline-flex; align-items:center; gap:4px; margin-right:12px;">'
        f'<span style="width:8px; height:8px; border-radius:2px; background:{color}; display:inline-block;"></span>'
        f'<span style="font-size:11px; opacity:0.75;">{ATTENDANCE_STATUS_LABELS[key]}</span>'
        f'</span>'
        for key, color in ATTENDANCE_STATUS_COLORS.items()
    )
    st.markdown(f'<div style="margin:2px 0 0 0;">{legend_items}</div>', unsafe_allow_html=True)


def employee_portal():
    inject_app_css()
    ensure_database()
    show_flash()

    employees_all, leave_all, credit_all, _ = get_clean_state_frames()

    # --- Sidebar: identity + logout only (no filters, no admin tools) ---
    if os.path.exists(LOGO_PATH):
        c_img1, c_img2, c_img3 = st.sidebar.columns([1, 2, 1])
        with c_img2: st.sidebar.image(LOGO_PATH, use_container_width=True)

    current_usr = str(st.session_state.get('current_user') or 'User').capitalize()
    st.sidebar.markdown(
        f"<div style='background: var(--background-color); padding: 8px; border-radius: 8px; "
        f"border: 0.5px solid var(--secondary-background-color); margin-bottom: 20px; text-align: center;'>"
        f"<h3 style='margin: 0; font-size: 14px;'>Welcome, {current_usr}</h3>"
        f"<p style='margin: 0; font-size: 12px; color: var(--primary-color, #0064E0); font-weight: 600;'>Employee Access</p>"
        f"</div>",
        unsafe_allow_html=True
    )
    st.sidebar.caption("This portal only shows your own records. For other requests, please contact your administrator.")
    if st.sidebar.button("Log Out", use_container_width=True, key="employee_portal_logout"):
        perform_logout()

    employee_id = st.session_state.get("current_employee_id")
    my_employee = employees_all[employees_all["id"] == employee_id] if employee_id is not None and not employees_all.empty else employees_all.iloc[0:0]

    if my_employee.empty:
        st.title("My Service Record")
        st.warning("Your account isn't linked to an employee record yet. Please ask your administrator to link your portal account under Manage Employees.")
        return

    # An employee_id being present is NOT enough on its own - it only means
    # someone (possibly the account holder themselves, via self-registration)
    # claimed this employee record. Real data is withheld until an admin has
    # explicitly approved the link. See ACCOUNT_STATUS_* in form6_auth.py.
    account_status = st.session_state.get("current_account_status")
    if account_status == ACCOUNT_STATUS_PENDING:
        st.title("My Service Record")
        st.info(
            "Your account is awaiting administrator approval. Once approved, you'll be able to see "
            "your own leave, attendance, and service credit records here. Please check back later, or "
            "contact your administrator if this is taking longer than expected."
        )
        return
    if account_status == ACCOUNT_STATUS_REJECTED:
        st.title("My Service Record")
        st.error(
            "Your account's employee link was not approved. Please contact your administrator if you "
            "believe this is a mistake."
        )
        return

    emp_row = my_employee.iloc[0]
    emp_name = safe_text(emp_row.get("employee_label")) or safe_text(emp_row.get("display_name"))
    emp_grade = safe_text(emp_row.get("grade")) or "-"
    emp_position = safe_text(emp_row.get("position")) or "-"

    # =========================================================================
    # HEADER - simple name/grade/position banner (replaces the old cover
    # photo + profile picture header)
    # =========================================================================
    st.markdown(f"""
    <div style="padding: 20px 4px 8px 4px;">
        <h1 style="margin: 0; padding: 0; font-size: 28px; line-height: 1.2;">{emp_name}</h1>
        <p style="margin: 4px 0 0 0; padding: 0; font-size: 16px; opacity: 0.8;">Grade: {emp_grade} | Position: {emp_position}</p>
    </div>
    """, unsafe_allow_html=True)

    st.markdown(
        '<div class="small-note">This page shows your own leave, attendance, and credit records. '
        'No one else can see this - only you and the school administrator.</div>',
        unsafe_allow_html=True,
    )

    # =========================================================================
    # DATA PREPARATION (moved up so the calendar below has what it needs)
    # =========================================================================
    my_leaves = leave_all[leave_all["employee_id"] == employee_id].copy()
    my_credits = credit_all[credit_all["employee_id"] == employee_id].copy()
    my_summary = get_summary_safe(my_employee, my_leaves, my_credits)
    attendance_all = get_attendance_frame()

    # =========================================================================
    # MY ATTENDANCE CALENDAR - color-coded Present / Late / Absent / On Leave
    # =========================================================================
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.subheader("My Attendance Calendar")
    st.caption("Color-coded by day. Approved leave always takes priority over a biometrics Absent reading.")

    if attendance_all.empty and my_leaves.empty:
        st.info("No attendance or leave records have been added yet. Check back after your school's monthly attendance is uploaded.")
    else:
        my_attendance_for_months = attendance_all[attendance_all["employee_id"] == employee_id] if not attendance_all.empty else attendance_all

        available_months = set()
        if not my_attendance_for_months.empty:
            attendance_dates = pd.to_datetime(my_attendance_for_months["record_date"], errors="coerce").dropna()
            available_months.update((d.year, d.month) for d in attendance_dates)
        if not my_leaves.empty:
            filing_dates = pd.to_datetime(my_leaves["date_of_filing"], errors="coerce").dropna()
            available_months.update((d.year, d.month) for d in filing_dates)
        # Always include the current month, even with no data yet, so the
        # calendar has something sensible to show on a brand-new account.
        today = date.today()
        available_months.add((today.year, today.month))

        month_names = ["January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]
        sorted_months = sorted(available_months, reverse=True)
        month_labels = {f"{month_names[m-1]} {y}": (y, m) for y, m in sorted_months}

        # Narrow column so the compact grid doesn't get stretched full-width
        # by Streamlit's layout - Google Calendar's month-mini-view is small
        # and dense, not a banner-sized element.
        cal_col, _spacer_col = st.columns([0.42, 0.58])
        with cal_col:
            selected_label = st.selectbox("Month", list(month_labels.keys()), index=0, key="portal_calendar_month", label_visibility="collapsed")
            selected_year, selected_month = month_labels[selected_label]

            day_status = build_attendance_calendar_days(attendance_all, my_leaves, employee_id, selected_year, selected_month)

            _render_attendance_legend()
            st.markdown(_render_attendance_calendar_html(selected_year, selected_month, day_status), unsafe_allow_html=True)

            if day_status:
                present_count = sum(1 for v in day_status.values() if v == "PRESENT")
                late_count = sum(1 for v in day_status.values() if v == "LATE")
                absent_count = sum(1 for v in day_status.values() if v == "ABSENT")
                on_leave_count = sum(1 for v in day_status.values() if v == "ON_LEAVE")
                st.caption(
                    f"{present_count} present · {late_count} late · {absent_count} absent · {on_leave_count} on leave"
                )
            else:
                st.caption("No attendance or leave records found for this month yet.")

    # --- Separate deductions from regular leaves ---
    is_deduction = my_leaves["leave_type"].astype(str).str.upper().isin(["ABSENT", "LATE"]) | (my_leaves.get("source_kind", "") == "biometrics")
    my_deductions = my_leaves[is_deduction].copy()
    my_regular_leaves = my_leaves[~is_deduction].copy()

    # --- Generate robust ledgers to ensure 100% accurate balances ---
    local_ledger = build_credit_ledger(my_leaves, my_credits, "LOCAL")
    if not local_ledger.empty:
        local_ledger["credit_scope_label"] = "LOCAL"
    local_bal = local_ledger.iloc[-1]["running_balance"] if not local_ledger.empty else 0.0

    div_ledger = build_credit_ledger(my_leaves, my_credits, "DIVISION")
    if not div_ledger.empty:
        div_ledger["credit_scope_label"] = "DIVISION"
    div_bal = div_ledger.iloc[-1]["running_balance"] if not div_ledger.empty else 0.0

    # BUG FIX: build_credit_ledger's running_balance is the raw cumulative
    # sum and can go negative (e.g. more credit used than ever earned).
    # get_summary_safe() - used everywhere else balances are shown in this
    # app (admin Dashboard, Manage Employees, etc.) - floors balances at 0.
    # Without this clip, an employee could see a negative balance here that
    # no admin view would ever show for the same person. Only the headline
    # Overview number is clipped; the ledger rows below still show the real
    # running balance history unclipped, for transparency.
    local_bal = max(0.0, local_bal)
    div_bal = max(0.0, div_bal)

    # =========================================================================
    # OVERVIEW - Balances calculated directly from the ledger for accuracy
    # =========================================================================
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.subheader("OVERVIEW")
    st.caption("A quick summary of your leave and credits. See the sections below for the full history.")

    leave_used = float(my_summary.iloc[0].get('leave_days', 0) or 0) if not my_summary.empty else 0.0
    cols = st.columns(3)
    cols[0].metric("Leave Days Used", f"{leave_used:.2f}", help="How many days of leave you have used in total.")
    cols[1].metric("Local Credits Left", f"{local_bal:.2f}", help="Your remaining Local service credit balance.")
    cols[2].metric("Division Credits Left", f"{div_bal:.2f}", help="Your remaining Division service credit balance.")

    # =========================================================================
    # CREDIT LEDGER - Unified table of earned credits (Scope, Date, Units, Bal)
    # =========================================================================
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.subheader("My Credits Earned")
    st.caption("Service credits you've earned from seminars, trainings, or other activities.")

    ledgers_to_concat = []
    if not local_ledger.empty: ledgers_to_concat.append(local_ledger)
    if not div_ledger.empty: ledgers_to_concat.append(div_ledger)

    if ledgers_to_concat:
        combined_ledger = pd.concat(ledgers_to_concat, ignore_index=True)
        # Filter for strictly earned credits
        earned_ledger = combined_ledger[combined_ledger["change"] > 0].copy()

        if not earned_ledger.empty:
            # Sort by date to unify the timeline
            earned_ledger = earned_ledger.sort_values(by="date", ascending=False, na_position="last")

            with st.expander(f"See my {len(earned_ledger)} earned credit(s)", expanded=False):
                h_cols = st.columns([0.15, 0.20, 0.35, 0.15, 0.15])
                h_cols[0].markdown("**Type**")
                h_cols[1].markdown("**Date**")
                h_cols[2].markdown("**What For**")
                h_cols[3].markdown("**Credits**")
                h_cols[4].markdown("**Balance After**")
                st.divider()

                for _, row in earned_ledger.iterrows():
                    r_cols = st.columns([0.15, 0.20, 0.35, 0.15, 0.15])
                    r_cols[0].write(row.get("credit_scope_label", ""))
                    r_cols[1].write(safe_text(row.get("date")))

                    desc = str(row.get("description", ""))
                    if desc.startswith("Earned - "):
                        desc = desc.replace("Earned - ", "", 1)
                    r_cols[2].write(desc)

                    r_cols[3].write(f"{float(row.get('change', 0) or 0):.2f}")
                    r_cols[4].write(f"{float(row.get('running_balance', 0) or 0):.2f}")
        else:
            st.info("You don't have any earned credits on file yet.")
    else:
        st.info("You don't have any earned credits on file yet.")

    # =========================================================================
    # MY RELIEVER POINTS - only shown if this employee has ever logged any,
    # so employees who've never relieved don't see an empty section.
    # =========================================================================
    my_reliever_progress = reliever_progress_for_employee(employee_id)
    if my_reliever_progress["total_points"] > 0:
        st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
        st.subheader("My Reliever Points")
        st.caption(
            f"1 point = {my_reliever_progress['minutes_per_point']:.0f} minutes of relieving duty. "
            f"Every {my_reliever_progress['points_per_credit']:.0f} points earns 1 LOCAL service credit "
            f"automatically - already counted in your Local Credits above."
        )
        rp_cols = st.columns(3)
        rp_cols[0].metric("Total Points Logged", f"{my_reliever_progress['total_points']:.2f}")
        rp_cols[1].metric("Credits Earned From Points", int(my_reliever_progress["credits_issued"]))
        rp_cols[2].metric(
            "Toward Next Credit",
            f"{my_reliever_progress['points_carried_over']:.2f} / {my_reliever_progress['points_per_credit']:.0f}",
        )

        all_reliever = load_reliever_points()
        my_reliever_sessions = all_reliever[all_reliever["employee_id"] == employee_id] if not all_reliever.empty else all_reliever
        if not my_reliever_sessions.empty:
            display_reliever = my_reliever_sessions.sort_values("entry_date", ascending=False, na_position="last")
            with st.expander(f"See my {len(display_reliever)} reliever session(s)", expanded=False):
                h_cols = st.columns([0.20, 0.15, 0.45, 0.20])
                h_cols[0].markdown("**Date**")
                h_cols[1].markdown("**Points**")
                h_cols[2].markdown("**Notes**")
                h_cols[3].markdown("**Hours**")
                st.divider()
                for _, sess_row in display_reliever.iterrows():
                    r_cols = st.columns([0.20, 0.15, 0.45, 0.20])
                    r_cols[0].write(safe_text(sess_row.get("entry_date")))
                    r_cols[1].write(f"{float(sess_row.get('points', 0) or 0):.2f}")
                    r_cols[2].write(sess_row.get("notes", "") or "-")
                    r_cols[3].write(f"{points_to_hours(sess_row.get('points', 0), my_reliever_progress['minutes_per_point']):.2f}")

    # =========================================================================
    # LEAVE LEDGER - Only shows standard approved leaves
    # =========================================================================
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.subheader("My Leave History")
    st.caption("Your approved leave requests, such as Vacation Leave or Sick Leave.")
    if my_regular_leaves.empty:
        st.info("You don't have any leave records on file yet.")
    else:
        display_leaves = my_regular_leaves.sort_values("date_of_filing", ascending=False, na_position="last")
        with st.expander(f"See my {len(display_leaves)} leave record(s)", expanded=False):
            h_cols = st.columns([0.15, 0.15, 0.20, 0.15, 0.20, 0.15])
            h_cols[0].markdown("**Date Filed**")
            h_cols[1].markdown("**Month**")
            h_cols[2].markdown("**Type of Leave**")
            h_cols[3].markdown("**Days**")
            h_cols[4].markdown("**Dates Covered**")
            h_cols[5].markdown("**Credit Used**")
            st.divider()
            for _, leave_row in display_leaves.iterrows():
                r_cols = st.columns([0.15, 0.15, 0.20, 0.15, 0.20, 0.15])
                r_cols[0].write(safe_text(leave_row.get("date_of_filing")))
                r_cols[1].write(leave_row.get("month", ""))
                r_cols[2].write(leave_row.get("leave_type", ""))
                r_cols[3].write(f"{leave_row.get('total_days', 0):.2f}")
                r_cols[4].write(leave_row.get("inclusive_dates", "") or "-")
                r_cols[5].write(leave_row.get("service_credit_availed", "") or "-")

    # =========================================================================
    # DEDUCTION LEDGER - Shows absences and tardiness (Biometrics)
    # =========================================================================
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.subheader("My Absences and Lates")
    st.caption("Times you were marked absent or late, and the credits deducted for them.")
    if my_deductions.empty:
        st.info("You don't have any absences or lates on file. Good job!")
    else:
        display_deductions = my_deductions.sort_values("date_of_filing", ascending=False, na_position="last")
        with st.expander(f"See my {len(display_deductions)} record(s)", expanded=False):
            h_cols = st.columns([0.15, 0.15, 0.20, 0.15, 0.20, 0.15])
            h_cols[0].markdown("**Date**")
            h_cols[1].markdown("**Month**")
            h_cols[2].markdown("**Type**")
            h_cols[3].markdown("**Credits Deducted**")
            h_cols[4].markdown("**Dates Covered**")
            h_cols[5].markdown("**Taken From**")
            st.divider()
            for _, ded_row in display_deductions.iterrows():
                r_cols = st.columns([0.15, 0.15, 0.20, 0.15, 0.20, 0.15])
                r_cols[0].write(safe_text(ded_row.get("date_of_filing")))
                r_cols[1].write(ded_row.get("month", ""))
                r_cols[2].write(ded_row.get("leave_type", ""))
                r_cols[3].write(f"{ded_row.get('total_days', 0):.2f}")
                r_cols[4].write(ded_row.get("inclusive_dates", "") or "-")
                r_cols[5].write(ded_row.get("service_credit_availed", "") or "-")

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.subheader("Save a Copy of My Records")
    st.caption("Get a copy of everything above as a file you can save or print.")
    if st.button("Prepare my file", key="gen_my_record", use_container_width=True):
        my_export_tables = {
            "My Summary": readable_view(my_summary),
            "My Leave Records": readable_view(my_regular_leaves, drop=["employee_id"]),
            "My Deductions": readable_view(my_deductions, drop=["employee_id"]),
            "My Service Credits": readable_view(my_credits, drop=["employee_id"]),
        }
        st.session_state["_my_record_bytes"] = export_workbook_bytes(my_export_tables)
    if "_my_record_bytes" in st.session_state:
        st.download_button(
            "Save my file to my computer",
            data=st.session_state["_my_record_bytes"],
            file_name=f"{normalize_username(st.session_state.get('current_user') or 'employee')}_service_record.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            use_container_width=True,
            key="dl_my_record",
        )