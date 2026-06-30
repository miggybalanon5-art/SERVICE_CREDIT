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


# Color coding for the attendance calendar.
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
    """Builds a compact, minimal, Google-Calendar-style month grid.
    Uses CSS variables (var(--text-color)) to dynamically adapt to 
    Streamlit's Light and Dark modes seamlessly."""
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
            # 26 hex = ~15% opacity, looks clean in both dark and light mode
            cells.append(
                f'<div class="cal-cell{today_class}" style="background:{color}26;" title="{label}">'
                f'<span class="cal-daynum-colored" style="color:{color};">{day_num}</span>'
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
    .cal-grid-wrapper { margin: 4px 0; max-width: 260px; width: 100%; font-family: sans-serif; }
    .cal-weekday-row { display: grid; grid-template-columns: repeat(7, 1fr); margin-bottom: 6px; }
    .cal-weekday { text-align: center; font-size: 10px; font-weight: 600; color: var(--text-color); opacity: 0.5; }
    .cal-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 2px; }
    .cal-cell {
        height: 32px;
        border-radius: 6px;
        display: flex;
        align-items: center;
        justify-content: center;
    }
    .cal-cell.cal-blank { background: transparent; }
    .cal-cell.cal-today { box-shadow: inset 0 0 0 1.5px var(--primary-color, #0064E0); }
    .cal-daynum { font-size: 12px; font-weight: 500; color: var(--text-color); opacity: 0.85; }
    .cal-daynum-colored { font-size: 12px; font-weight: 700; }

    @media (max-width: 768px) {
        .cal-grid-wrapper { max-width: 100%; }
        .cal-cell { height: 38px; }
    }
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
        f'<span style="display:inline-flex; align-items:center; gap:4px; margin-right:10px;">'
        f'<span style="width:8px; height:8px; border-radius:50%; background:{color}; display:inline-block;"></span>'
        f'<span style="font-size:10px; color: var(--text-color); opacity:0.7;">{ATTENDANCE_STATUS_LABELS[key]}</span>'
        f'</span>'
        for key, color in ATTENDANCE_STATUS_COLORS.items()
    )
    st.markdown(f'<div style="margin:4px 0 8px 0;">{legend_items}</div>', unsafe_allow_html=True)


def employee_portal():
    inject_app_css()
    ensure_database()
    show_flash()

    employees_all, leave_all, credit_all, _ = get_clean_state_frames()

    # --- Sidebar: identity + logout only ---
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

    account_status = st.session_state.get("current_account_status")
    if account_status == ACCOUNT_STATUS_PENDING:
        st.title("My Service Record")
        st.info(
            "Your account is awaiting administrator approval. Once approved, you'll be able to see "
            "your own leave, attendance, and service credit records here. Please check back later."
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
    # 1. HEADER - Grade, Name, Position at the very top
    # =========================================================================
    st.markdown(f"""
    <div style="padding: 10px 4px 15px 4px;">
        <p style="margin: 0; padding: 0; font-size: 12px; font-weight: 600; color: var(--primary-color); text-transform: uppercase;">{emp_grade}</p>
        <h1 style="margin: 0; padding: 0; font-size: 28px; line-height: 1.2; color: var(--text-color);">{emp_name}</h1>
        <p style="margin: 2px 0 0 0; padding: 0; font-size: 15px; color: var(--text-color); opacity: 0.7;">{emp_position}</p>
    </div>
    """, unsafe_allow_html=True)

    # =========================================================================
    # DATA PREPARATION 
    # =========================================================================
    my_leaves = leave_all[leave_all["employee_id"] == employee_id].copy()
    my_credits = credit_all[credit_all["employee_id"] == employee_id].copy()
    my_summary = get_summary_safe(my_employee, my_leaves, my_credits)
    attendance_all = get_attendance_frame()
    my_reliever_progress = reliever_progress_for_employee(employee_id)

    local_ledger = build_credit_ledger(my_leaves, my_credits, "LOCAL")
    if not local_ledger.empty:
        local_ledger["credit_scope_label"] = "LOCAL"
    local_bal = max(0.0, local_ledger.iloc[-1]["running_balance"] if not local_ledger.empty else 0.0)

    div_ledger = build_credit_ledger(my_leaves, my_credits, "DIVISION")
    if not div_ledger.empty:
        div_ledger["credit_scope_label"] = "DIVISION"
    div_bal = max(0.0, div_ledger.iloc[-1]["running_balance"] if not div_ledger.empty else 0.0)
    
    # Isolate regular leaves (ignoring deductions for the leave history tab)
    is_deduction = my_leaves["leave_type"].astype(str).str.upper().isin(["ABSENT", "LATE"]) | (my_leaves.get("source_kind", "") == "biometrics")
    my_regular_leaves = my_leaves[~is_deduction].copy()

    # =========================================================================
    # 2. OVERVIEW - Immediate summary including Reliever Points
    # Laid out as a 2x2 grid so it stays readable on narrow / mobile screens
    # instead of squeezing 4 metrics into one row.
    # =========================================================================
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.subheader("Overview")

    leave_used = float(my_summary.iloc[0].get('leave_days', 0) or 0) if not my_summary.empty else 0.0
    overview_row1 = st.columns(2)
    overview_row1[0].metric("Leave Days Used", f"{leave_used:.2f}")
    overview_row1[1].metric("Local Credits", f"{local_bal:.2f}")
    overview_row2 = st.columns(2)
    overview_row2[0].metric("Division Credits", f"{div_bal:.2f}")
    overview_row2[1].metric("Reliever Points", f"{my_reliever_progress['total_points']:.2f}")

    # =========================================================================
    # 3. MY ATTENDANCE CALENDAR
    # =========================================================================
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.subheader("Attendance")

    if attendance_all.empty and my_leaves.empty:
        st.info("No attendance or leave records have been added yet.")
    else:
        my_attendance_for_months = attendance_all[attendance_all["employee_id"] == employee_id] if not attendance_all.empty else attendance_all

        available_months = set()
        if not my_attendance_for_months.empty:
            attendance_dates = pd.to_datetime(my_attendance_for_months["record_date"], errors="coerce").dropna()
            available_months.update((d.year, d.month) for d in attendance_dates)
        if not my_leaves.empty:
            filing_dates = pd.to_datetime(my_leaves["date_of_filing"], errors="coerce").dropna()
            available_months.update((d.year, d.month) for d in filing_dates)
            
        today = date.today()
        available_months.add((today.year, today.month))

        month_names = ["January", "February", "March", "April", "May", "June",
                       "July", "August", "September", "October", "November", "December"]
        sorted_months = sorted(available_months, reverse=True)
        month_labels = {f"{month_names[m-1]} {y}": (y, m) for y, m in sorted_months}

        # On narrow screens the global mobile CSS stacks st.columns vertically,
        # so this still renders as: month picker on top, calendar below.
        cal_col, _spacer_col = st.columns([0.3, 0.7])
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
                st.markdown(
                    f'<div style="font-size: 10px; color: var(--text-color); opacity: 0.6; margin-top: 8px;">'
                    f'{present_count} present · {late_count} late<br>{absent_count} absent · {on_leave_count} on leave'
                    f'</div>', 
                    unsafe_allow_html=True
                )
            else:
                st.markdown('<div style="font-size: 10px; opacity: 0.6;">No records found.</div>', unsafe_allow_html=True)

    # =========================================================================
    # 4. TABBED DATA LEDGERS
    # Rendered as real st.dataframe tables (instead of manual st.columns rows)
    # so they stay tabular with native horizontal scrolling on mobile, rather
    # than collapsing into a stacked list under the global mobile column CSS.
    # =========================================================================
    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    
    tab_credits, tab_leaves, tab_reliever, tab_export = st.tabs([
        "Earned Credits", "Leave History", "Reliever Points", "Export Records"
    ])

    # --- TAB 1: CREDIT LEDGER ---
    with tab_credits:
        ledgers_to_concat = []
        if not local_ledger.empty: ledgers_to_concat.append(local_ledger)
        if not div_ledger.empty: ledgers_to_concat.append(div_ledger)

        if ledgers_to_concat:
            combined_ledger = pd.concat(ledgers_to_concat, ignore_index=True)
            earned_ledger = combined_ledger[combined_ledger["change"] > 0].copy()

            if not earned_ledger.empty:
                earned_ledger = earned_ledger.sort_values(by="date", ascending=False, na_position="last")

                def _clean_what_for(desc) -> str:
                    text = str(desc or "")
                    if text.startswith("Earned - "):
                        text = text.replace("Earned - ", "", 1)
                    return text

                credits_table = pd.DataFrame({
                    "Type": earned_ledger.get("credit_scope_label", ""),
                    "Date": earned_ledger.get("date", "").apply(safe_text),
                    "What For": earned_ledger.get("description", "").apply(_clean_what_for),
                    "Credits": earned_ledger.get("change", 0).astype(float).round(2),
                })
                st.dataframe(
                    credits_table,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Credits": st.column_config.NumberColumn("Credits", format="%.2f"),
                    },
                )
            else:
                st.info("You don't have any earned credits on file yet.")
        else:
            st.info("You don't have any earned credits on file yet.")

    # --- TAB 2: LEAVE HISTORY ---
    with tab_leaves:
        if my_regular_leaves.empty:
            st.info("You don't have any leave records on file yet.")
        else:
            display_leaves = my_regular_leaves.sort_values("date_of_filing", ascending=False, na_position="last")
            leave_table = pd.DataFrame({
                "Date Filed": display_leaves.get("date_of_filing", "").apply(safe_text),
                "Month": display_leaves.get("month", ""),
                "Type of Leave": display_leaves.get("leave_type", ""),
                "Days": display_leaves.get("total_days", 0).astype(float).round(2),
                "Dates Covered": display_leaves.get("inclusive_dates", "").replace("", "-").fillna("-"),
                "Credit Used": display_leaves.get("service_credit_availed", "").replace("", "-").fillna("-"),
            })
            st.dataframe(
                leave_table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Days": st.column_config.NumberColumn("Days", format="%.2f"),
                },
            )

    # --- TAB 3: RELIEVER POINTS ---
    with tab_reliever:
        all_reliever = load_reliever_points()
        my_reliever_sessions = all_reliever[all_reliever["employee_id"] == employee_id] if not all_reliever.empty else all_reliever
        
        if not my_reliever_sessions.empty:
            st.caption(
                f"Every {my_reliever_progress['points_per_credit']:.0f} points earns 1 LOCAL service credit. "
                f"You have {my_reliever_progress['points_carried_over']:.2f} points toward your next credit."
            )
            display_reliever = my_reliever_sessions.sort_values("entry_date", ascending=False, na_position="last")
            reliever_table = pd.DataFrame({
                "Date": display_reliever.get("entry_date", "").apply(safe_text),
                "Points": display_reliever.get("points", 0).astype(float).round(2),
                "Notes": display_reliever.get("notes", "").replace("", "-").fillna("-"),
                "Hours": display_reliever.get("points", 0).apply(
                    lambda p: points_to_hours(p, my_reliever_progress['minutes_per_point'])
                ).astype(float).round(2),
            })
            st.dataframe(
                reliever_table,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Points": st.column_config.NumberColumn("Points", format="%.2f"),
                    "Hours": st.column_config.NumberColumn("Hours", format="%.2f"),
                },
            )
        else:
            st.info("You don't have any reliever sessions logged yet.")

    # --- TAB 4: EXPORT ---
    with tab_export:
        st.write("Get a complete copy of your records as a file you can save or print.")
        if st.button("Prepare my file", key="gen_my_record", use_container_width=True):
            my_export_tables = {
                "My Summary": readable_view(my_summary),
                "My Leave Records": readable_view(my_regular_leaves, drop=["employee_id"]),
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
