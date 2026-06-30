"""
"Newly Encoded" tab: records added by hand in this app, rather than imported
from the old workbooks.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from form6_ui import CREDIT_DISPLAY_RENAME, CREDIT_INTERNAL_COLS, LEAVE_DISPLAY_RENAME, LEAVE_INTERNAL_COLS, readable_view


def render(leave_filtered: pd.DataFrame, credit_filtered: pd.DataFrame) -> None:
    st.subheader("Newly Encoded Records")
    st.caption("Records added by hand in this app, rather than imported from the old workbooks.")
    st.markdown("### Leaves")
    manual_leaves = leave_filtered[leave_filtered["source_kind"] == "manual"].sort_values("date_of_filing", ascending=False, na_position="last")
    if manual_leaves.empty:
        st.info("No manually encoded leave records.")
    else:
        st.dataframe(readable_view(manual_leaves, drop=LEAVE_INTERNAL_COLS, rename=LEAVE_DISPLAY_RENAME), use_container_width=True, hide_index=True)
    st.markdown("### Service Credits")
    manual_credits = credit_filtered[credit_filtered["source_kind"] == "manual"].sort_values("event_date", ascending=False, na_position="last")
    if manual_credits.empty:
        st.info("No manually encoded service credit records.")
    else:
        st.dataframe(readable_view(manual_credits, drop=CREDIT_INTERNAL_COLS, rename=CREDIT_DISPLAY_RENAME), use_container_width=True, hide_index=True)
