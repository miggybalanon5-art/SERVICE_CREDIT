"""
"Ongoing/Upcoming Leave" tab: leave records filed for today or a future date.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import streamlit as st

from form6_ui import LEAVE_DISPLAY_RENAME, LEAVE_INTERNAL_COLS, readable_view


def render(leave_filtered: pd.DataFrame) -> None:
    st.subheader("Upcoming Leave")
    st.caption("Leave records filed for today or a future date.")
    # Biometrics-sourced deductions (ABSENT/LATE) are not leave the employee
    # filed - exclude them here the same way they're excluded from the Leave
    # Ledger, so an absence/lateness can't show up looking like upcoming leave.
    leave_only = leave_filtered[leave_filtered.get("source_kind", "") != "biometrics"]
    today_ts = pd.Timestamp(date.today())
    upcoming = leave_only.copy()
    upcoming["parsed_date"] = pd.to_datetime(upcoming["date_of_filing"], errors="coerce")
    upcoming = upcoming[upcoming["parsed_date"] >= today_ts].drop(columns=["parsed_date"], errors="ignore")
    upcoming = upcoming.sort_values("date_of_filing", na_position="last")
    if upcoming.empty:
        st.info("No upcoming leaves.")
    else:
        st.dataframe(readable_view(upcoming, drop=LEAVE_INTERNAL_COLS, rename=LEAVE_DISPLAY_RENAME), use_container_width=True, hide_index=True)
