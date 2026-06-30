From __future__ import annotations

import base64
import html
import os
import sys
import time

import pandas as pd
import streamlit as st

from form6_data import grade_sort_key
from form6_store import db_counts, ensure_database
from form6_cache import get_attendance_frame, get_clean_state_frames
from form6_auth import (
    LOGO_PATH,
    clear_session_query_token,
    get_session_query_token,
    load_users,
    log_action,
    login_page,
    perform_logout,
    remove_browser_session,
    restore_browser_session,
    SESSION_TIMEOUT_MINUTES,
)
from form6_filters import apply_filters
from form6_ui import date_bounds, inject_app_css, show_flash, show_pending_toast
from employee_portal import employee_portal

import tab_employees
import tab_upcoming_leave
import tab_newly_encoded
import tab_import_backup
import tab_help_assistant

# ----------------------------------------------------------------------------
# PATH SETUP & CONSTANTS
# ----------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PAGE_TITLE = "Service Credit Tracker"

st.set_page_config(page_title=PAGE_TITLE, layout="wide")


# ----------------------------------------------------------------------------
# MAIN CORE TRACKER APP (sidebar, filters, tab routing)
# ----------------------------------------------------------------------------
def main_app():
    inject_app_css()

    ensure_database()
    show_flash()
    show_pending_toast()

    employees_all, leave_all, credit_all, import_log = get_clean_state_frames()
    attendance_all = get_attendance_frame()

    # --- BRAVE-INSPIRED BANNER FOR SIDEBAR WITH SCHOOL LOGO ---
    logo_b64 = ""
    if os.path.exists(LOGO_PATH):
        with open(LOGO_PATH, "rb") as _lf:
            logo_b64 = base64.b64encode(_lf.read()).decode()

    logo_img_html = f'<img src="data:image/png;base64,{logo_b64}" alt="CNHS Logo">' if logo_b64 else ""

    st.sidebar.markdown(
        f"""
        <style>
            .brave-style-header {{
                position: relative; width: 100%; background-color: transparent;
                margin-bottom: 15px; border-radius: 12px; overflow: hidden;
                border: 1px solid rgba(0,0,0,0.06); box-shadow: 0 1px 2px rgba(0,0,0,0.08);
            }}
            .brave-top-nav {{
                display: flex; flex-direction: column; justify-content: center; align-items: center;
                text-align: center; padding: 18px 10px;
                background-color: color-mix(in srgb, var(--secondary-background-color) 85%, transparent);
                backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px);
                border-bottom: 1px solid color-mix(in srgb, var(--text-color) 10%, transparent);
            }}
            .brave-nav-left {{ display: flex; flex-direction: column; align-items: center; gap: 10px; }}
            .brave-nav-left img {{ height: 42px; width: auto; object-fit: contain; }}
            .brave-nav-title {{
                color: var(--text-color); font-size: 14px; font-weight: 700; 
                letter-spacing: -0.02em; display: flex; flex-direction: column; align-items: center; gap: 4px; line-height: 1.2;
            }}
            .brave-nav-subtitle {{ color: var(--primary-color, #0064E0); font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; }}
            .brave-hero-banner {{
                position: relative; height: 15px; background: var(--background-color);
                display: flex; justify-content: center; align-items: center; overflow: hidden;
                -webkit-mask-image: linear-gradient(to bottom, black 0%, transparent 100%);
                mask-image: linear-gradient(to bottom, black 0%, transparent 100%);
            }}
        </style>
        <div class="brave-style-header">
            <div class="brave-top-nav">
                <div class="brave-nav-left">
                    {logo_img_html}
                    <div class="brave-nav-title">Calauag National High School <span class="brave-nav-subtitle">Service Credit Tracker</span></div>
                </div>
            </div>
            <div class="brave-hero-banner"></div>
        </div>
        """,
        unsafe_allow_html=True
    )

    current_usr = html.escape(str(st.session_state.get('current_user') or 'User').capitalize())
    role_badge = "Admin" if st.session_state.get('current_role') == "admin" else "Staff"

    st.sidebar.markdown(
        f"<div style='background: var(--background-color); padding: 8px; border-radius: 8px; "
        f"border: 0.5px solid var(--secondary-background-color); margin-bottom: 20px; text-align: center; '>"
        f"<h3 style='margin: 0; font-size: 14px;'>Welcome, {current_usr}</h3>"
        f"<p style='margin: 0; font-size: 12px; color: var(--primary-color, #0064E0); font-weight: 600;'>{role_badge} Access</p>"
        f"</div>",
        unsafe_allow_html=True
    )

    sidebar = st.sidebar
    sidebar.header("Filters")
    sidebar.caption("Narrow down what you see across every tab.")

    grade_options = sorted([value for value in employees_all["grade"].dropna().unique().tolist() if value], key=grade_sort_key)
    employee_options_all = [value for value in employees_all["employee_label"].dropna().unique().tolist() if value]
    origin_options = sorted(
        set([value for value in leave_all.get("source_kind", pd.Series(dtype=str)).dropna().unique().tolist() if value])
        | set([value for value in credit_all.get("source_kind", pd.Series(dtype=str)).dropna().unique().tolist() if value])
    )
    leave_type_options = sorted([value for value in leave_all.get("leave_type", pd.Series(dtype=str)).dropna().unique().tolist() if value])
    credit_scope_options = sorted([value for value in credit_all.get("credit_scope", pd.Series(dtype=str)).dropna().unique().tolist() if value])
    min_date, max_date = date_bounds(leave_all, credit_all)

    if st.session_state.pop("_sync_manage_to_filter", False):
        st.session_state["filter_search_term"] = st.session_state.get("manage_search_term", "")

    if "manage_search_term" not in st.session_state:
        st.session_state["manage_search_term"] = st.session_state.get("filter_search_term", "")

    def _sync_sidebar_search_to_manage() -> None:
        st.session_state["manage_search_term"] = st.session_state.get("filter_search_term", "")

    search_term = sidebar.text_input(
        "Search name, position, or grade",
        placeholder="Type to search...",
        key="filter_search_term",
        on_change=_sync_sidebar_search_to_manage,
    )

    with sidebar.expander("Grade & employee", expanded=False):
        selected_grades = st.multiselect("Grades", grade_options, default=grade_options, key="filter_grades")
        selected_employees = st.multiselect("Employees", employee_options_all, default=employee_options_all, key="filter_employees")

    with sidebar.expander("Record type", expanded=False):
        selected_origins = st.multiselect("Record origin", origin_options, default=origin_options, key="filter_origins")
        selected_leave_types = st.multiselect("Leave types", leave_type_options, default=leave_type_options, key="filter_leave_types")
        selected_credit_scopes = st.multiselect("Credit scopes", credit_scope_options, default=credit_scope_options, key="filter_credit_scopes")

    if min_date is not None and max_date is not None:
        with sidebar.expander("Date range", expanded=False):
            date_input = st.date_input(
                "Leave date range",
                value=(min_date.date(), max_date.date()),
                min_value=min_date.date(),
                max_value=max_date.date(),
                key="filter_date_range",
            )
            if isinstance(date_input, tuple) and len(date_input) == 2:
                selected_date_range = (pd.to_datetime(date_input[0]), pd.to_datetime(date_input[1]))
            else:
                selected_date_range = (None, None)
    else:
        selected_date_range = (None, None)

    employees_filtered, summary_filtered, leave_filtered, credit_filtered = apply_filters(
        employees_all, leave_all, credit_all, selected_grades, selected_employees, selected_origins,
        selected_leave_types, selected_credit_scopes, search_term, selected_date_range,
    )

    sidebar.markdown("---")
    with sidebar.expander("Database info", expanded=False):
        counts = db_counts()
        st.write(f"{counts['employees']:,} employees")
        st.write(f"{counts['leave_entries']:,} leave rows")
        st.write(f"{counts['service_credits']:,} credit rows")

    sidebar.markdown("---")
    sidebar.header("Navigation")
    tab_options = ["Employees", "Ongoing/Upcoming Leave", "Newly Encoded", "Import / Backup", "Help & Assistant"]
    active_tab = sidebar.radio("Go to", tab_options, label_visibility="collapsed", key="main_nav_tab")

    # Logout Button on Sidebar
    st.sidebar.markdown('<br>', unsafe_allow_html=True)
    if st.sidebar.button("Log Out", use_container_width=True):
        perform_logout()

    st.header(PAGE_TITLE)
    st.markdown(
        '<div class="small-note">Leave and service credit records are encoded and stored directly in this app. '
        'The old workbooks are used only as a one-time import source.</div>',
        unsafe_allow_html=True,
    )
    if search_term.strip():
        st.caption(f'Showing results matching: "{search_term.strip()}"')

    # Each tab is rendered by its own module
    # actually does any work, which keeps a click on (say) "Import / Backup"
    # from re-running all the Employees-tab dataframe rendering as well.
    if active_tab == "Employees":
        tab_employees.render(employees_all, leave_all, credit_all, employees_filtered, summary_filtered, leave_filtered, credit_filtered, attendance_all)
    elif active_tab == "Ongoing/Upcoming Leave":
        tab_upcoming_leave.render(leave_filtered)
    elif active_tab == "Newly Encoded":
        tab_newly_encoded.render(leave_filtered, credit_filtered)
    elif active_tab == "Import / Backup":
        tab_import_backup.render(employees_all, leave_all)
    elif active_tab == "Help & Assistant":
        tab_help_assistant.render()


# ----------------------------------------------------------------------------
# APP EXECUTION ENTRY POINT
# ----------------------------------------------------------------------------
def main():
    if "logged_in" not in st.session_state: st.session_state.logged_in = False
    if "current_user" not in st.session_state: st.session_state.current_user = None
    if "current_role" not in st.session_state: st.session_state.current_role = "user"
    if "current_employee_id" not in st.session_state: st.session_state.current_employee_id = None
    if "last_activity" not in st.session_state: st.session_state.last_activity = time.time()
    if "failed_attempts" not in st.session_state: st.session_state.failed_attempts = 0
    if "lockout_time" not in st.session_state: st.session_state.lockout_time = 0
    if "session_token" not in st.session_state: st.session_state.session_token = get_session_query_token()

    if not st.session_state.logged_in:
        restored_session = restore_browser_session(st.session_state.session_token)
        if restored_session:
            st.session_state.logged_in = True
            st.session_state.current_user = restored_session["username"]
            st.session_state.current_role = restored_session["role"]
            st.session_state.current_employee_id = restored_session.get("employee_id")
            st.session_state.session_token = restored_session["token"]
        elif st.session_state.session_token:
            st.session_state.session_token = ""
            clear_session_query_token()

    if st.session_state.logged_in:
        if time.time() - st.session_state.last_activity > (SESSION_TIMEOUT_MINUTES * 60):
            log_action(st.session_state.current_user, "TIMEOUT", "Session expired due to inactivity.")
            remove_browser_session(st.session_state.get("session_token", ""))
            clear_session_query_token()
            st.session_state.logged_in = False
            st.session_state.current_user = None
            st.session_state.current_role = "user"
            st.session_state.current_employee_id = None
            st.session_state.session_token = ""
            st.warning(f"Session timed out after {SESSION_TIMEOUT_MINUTES} minutes of inactivity. Please log in again.")
            st.rerun()
        st.session_state.last_activity = time.time()

    if not st.session_state.logged_in:
        login_page()
    else:
        if st.session_state.current_role == "employee":
            employee_portal()
        else:
            main_app()

if __name__ == "__main__":
    main()


that the app.py edit it