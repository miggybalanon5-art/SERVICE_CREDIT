"""
Streamlit caching layer for Form6 data access.

WHY THIS FILE EXISTS
---------------------
form6_store.load_state() re-reads three full SQL joins into pandas DataFrames
every time it is called. Without caching, Streamlit calls it on *every single
rerun* - every keystroke in the search box, every filter change, every form
submit - which is the main source of the app feeling slow.

This module wraps load_state() (and the cleaned/normalized variant used by
both the admin app and the employee portal) with @st.cache_data, keyed on the
database file's last-modified time. That means:
  - Typing in search, switching filters, switching tabs: instant, because the
    DB hasn't changed, so the cached DataFrames are reused.
  - Saving, deleting, importing, undoing: the DB file's mtime changes, so the
    cache key changes and the next call re-reads fresh data automatically.

invalidate_state_cache() is also provided for explicit use right after a
write, so the very next read in the same rerun (not just the next rerun)
sees fresh data.
"""

from __future__ import annotations

import os

import pandas as pd
import streamlit as st

from form6_store import DB_PATH, load_attendance_records, load_state


def _db_mtime_token() -> float:
    """Cache-busting token: changes the instant the DB file is written to."""
    try:
        return os.path.getmtime(DB_PATH)
    except OSError:
        return 0.0


@st.cache_data(show_spinner=False)
def _load_state_cached(_mtime_token: float):
    """Internal cached loader. The leading underscore on the arg name tells
    Streamlit not to hash it as part of the cache key by content - we pass
    the mtime float itself as the real key instead."""
    state = load_state()
    return state.employees, state.leave_entries, state.service_credits, state.import_log


@st.cache_data(show_spinner=False)
def _load_attendance_cached(_mtime_token: float):
    return load_attendance_records()


def get_attendance_frame() -> pd.DataFrame:
    """Cached equivalent of load_attendance_records(), used by the
    Attendance Summary tab. Keyed on the same DB mtime token as everything
    else, so an import (which changes the file) is picked up immediately."""
    token = _db_mtime_token()
    attendance = _load_attendance_cached(token)
    return attendance.copy()


def get_state_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Cached equivalent of load_state(), returned as four DataFrames
    (employees, leave_entries, service_credits, import_log)."""
    token = _db_mtime_token()
    employees, leave_entries, service_credits, import_log = _load_state_cached(token)
    # Cached DataFrames are shared across reruns - copy before handing out so
    # callers can safely mutate (e.g. the legacy "seed" -> "imported" rename)
    # without corrupting the cached objects for the next call.
    return employees.copy(), leave_entries.copy(), service_credits.copy(), import_log.copy()


def get_clean_state_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Cached + normalized state: legacy 'seed' source labels relabeled to
    'imported'. This is the shared loader used by both the admin app and the
    employee portal (previously app.py's load_clean_state())."""
    employees, leave_entries, service_credits, import_log = get_state_frames()
    employees = employees.replace(to_replace=r"(?i)seed", value="imported", regex=True)
    leave_entries = leave_entries.replace(to_replace=r"(?i)seed", value="imported", regex=True)
    service_credits = service_credits.replace(to_replace=r"(?i)seed", value="imported", regex=True)
    import_log = import_log.replace(to_replace=r"(?i)seed", value="imported", regex=True)
    return employees, leave_entries, service_credits, import_log


def invalidate_state_cache() -> None:
    """Call right after any write (insert/update/delete/import) so that even
    a read in the *same* rerun (before the mtime-based key would naturally
    change) sees fresh data. Cheap and safe to call defensively."""
    _load_state_cached.clear()
    _load_attendance_cached.clear()
