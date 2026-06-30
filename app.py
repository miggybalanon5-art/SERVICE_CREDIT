from __future__ import annotations

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
# PATH SETUP & CONFIGURATION
# ----------------------------------------------------------------------------
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

PAGE_TITLE = "Service Credit Tracker"

# CRITICAL: Sidebar collapsed by default for mobile-first responsiveness
st.set_page_config(
    page_title=PAGE_TITLE, 
    layout="wide", 
    initial_sidebar_state="collapsed"
)

# ----------------------------------------------------------------------------
# MAIN APP LOGIC
# ----------------------------------------------------------------------------
def main_app():
    # Inject the new Material Design CSS
    inject_app_css()

    ensure_database()
    show_flash()
    show_pending_toast()

    employees_all, leave_all, credit_all, import_log = get_clean_state_frames()
    attendance_all = get_attendance_frame()

    # --- SIDEBAR LOGO & WELCOME ---
    logo_b64 = ""
    if os.path.exists(LOGO_PATH):
        with open(LOGO_PATH, "rb") as _lf:
            logo_b64 = base64.b64encode(_lf.read()).decode()
    logo_img_html = f'<img src="data:image/png;base64,{logo_b64}" alt="CNHS Logo">' if logo_b64 else ""

    st.sidebar.markdown(f"""
        <div class="brave-style-header">
            <div class="brave-top-nav">
                {logo_img_html}
                <div class="brave-nav-title">CNHS <span class="brave-nav-subtitle">Service Credit Tracker</span></div>
            </div>
        </div>
    """, unsafe_allow_html=True)

    # --- FILTERS ---
    sidebar = st.sidebar
    sidebar.header("Filters")
    
    # ... [Keep your existing filter logic here: grade_options, search_term, date_input, etc.] ...
    # (The existing logic in your provided snippet is correct and functional)

    # --- TAB NAVIGATION ---
    tab_options = ["Employees", "Ongoing/Upcoming Leave", "Newly Encoded", "Import / Backup", "Help & Assistant"]
    active_tab = sidebar.radio("Go to", tab_options, label_visibility="collapsed", key="main_nav_tab")

    if sidebar.button("Log Out", use_container_width=True):
        perform_logout()

    # --- RENDER TABS ---
    st.header(PAGE_TITLE)
    
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
# SESSION ENTRY
# ----------------------------------------------------------------------------
def main():
    if "logged_in" not in st.session_state: st.session_state.logged_in = False
    
    # ... [Keep existing login/session management here] ...
    
    if not st.session_state.logged_in:
        login_page()
    else:
        if st.session_state.current_role == "employee":
            employee_portal()
        else:
            main_app()

if __name__ == "__main__":
    main()
