"""
Filtering logic for the Form 6 Tracker, with caching layered on top.

WHY THIS FILE EXISTS
---------------------
apply_filters() runs ~12 .str.casefold().str.contains() passes across the
employees/leaves/credits tables on every call. Previously this ran on every
single keystroke in the search box with no memoization at all - retyping the
same letter, or backspacing back to a term you'd already searched, recomputed
everything from scratch.

_apply_filters_cached() wraps the real filtering logic with @st.cache_data,
keyed on the actual filter values (grades, employees, search term, etc.) and
on the database's mtime token so a cache entry naturally goes stale the
moment the underlying data changes - not before.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st


def _filter_core(
    employees: pd.DataFrame,
    leaves: pd.DataFrame,
    credits: pd.DataFrame,
    grades: tuple[str, ...],
    employee_labels: tuple[str, ...],
    record_origins: tuple[str, ...],
    leave_types: tuple[str, ...],
    credit_scopes: tuple[str, ...],
    search_term: str,
    date_range: tuple,
):
    from form6_actions import get_summary_safe

    employees_filtered = employees.copy()
    if grades: employees_filtered = employees_filtered[employees_filtered["grade"].isin(grades)]
    if employee_labels: employees_filtered = employees_filtered[employees_filtered["employee_label"].isin(employee_labels)]

    if search_term:
        needle = search_term.casefold().strip()
        if needle:
            employees_filtered = employees_filtered[
                employees_filtered["employee_label"].str.casefold().str.contains(needle, na=False)
                | employees_filtered["display_name"].str.casefold().str.contains(needle, na=False)
                | employees_filtered["position"].str.casefold().str.contains(needle, na=False)
                | employees_filtered["grade"].str.casefold().str.contains(needle, na=False)
            ]

    employee_ids = employees_filtered["id"].tolist()
    leaves_filtered = leaves[leaves["employee_id"].isin(employee_ids)].copy()
    credits_filtered = credits[credits["employee_id"].isin(employee_ids)].copy()

    if record_origins:
        leaves_filtered = leaves_filtered[leaves_filtered["source_kind"].isin(record_origins)]
        credits_filtered = credits_filtered[credits_filtered["source_kind"].isin(record_origins)]

    if leave_types: leaves_filtered = leaves_filtered[leaves_filtered["leave_type"].isin(leave_types)]
    if credit_scopes: credits_filtered = credits_filtered[credits_filtered["credit_scope"].isin(credit_scopes)]

    if search_term:
        needle = search_term.casefold().strip()
        if needle:
            leaves_filtered = leaves_filtered[
                leaves_filtered["employee_label"].str.casefold().str.contains(needle, na=False)
                | leaves_filtered["leave_type"].str.casefold().str.contains(needle, na=False)
                | leaves_filtered["inclusive_dates"].str.casefold().str.contains(needle, na=False)
                | leaves_filtered["month"].str.casefold().str.contains(needle, na=False)
            ]
            credits_filtered = credits_filtered[
                credits_filtered["employee_label"].str.casefold().str.contains(needle, na=False)
                | credits_filtered["service_attended"].str.casefold().str.contains(needle, na=False)
                | credits_filtered["credit_scope"].str.casefold().str.contains(needle, na=False)
                | credits_filtered["event_date"].astype(str).str.casefold().str.contains(needle, na=False)
            ]

    start, end = date_range
    if start is not None:
        leaves_filtered = leaves_filtered[
            leaves_filtered["date_of_filing"].notna() & (leaves_filtered["date_of_filing"] >= start)
        ]
    if end is not None:
        leaves_filtered = leaves_filtered[
            leaves_filtered["date_of_filing"].notna() & (leaves_filtered["date_of_filing"] <= end)
        ]

    summary = get_summary_safe(employees_filtered, leaves_filtered, credits_filtered)
    return employees_filtered.reset_index(drop=True), summary, leaves_filtered.reset_index(drop=True), credits_filtered.reset_index(drop=True)


@st.cache_data(show_spinner=False)
def _apply_filters_cached(
    _employees: pd.DataFrame,
    _leaves: pd.DataFrame,
    _credits: pd.DataFrame,
    mtime_token: float,
    grades: tuple[str, ...],
    employee_labels: tuple[str, ...],
    record_origins: tuple[str, ...],
    leave_types: tuple[str, ...],
    credit_scopes: tuple[str, ...],
    search_term: str,
    date_range: tuple,
):
    """Cached filtering. Leading underscores on the DataFrame args tell
    Streamlit's hasher to skip hashing their full content (expensive for
    large tables) - mtime_token is the real cache key for "did the data
    change", while the rest of the args key on "did the filter change"."""
    return _filter_core(_employees, _leaves, _credits, grades, employee_labels, record_origins, leave_types, credit_scopes, search_term, date_range)


def apply_filters(
    employees: pd.DataFrame,
    leaves: pd.DataFrame,
    credits: pd.DataFrame,
    grades: list[str],
    employee_labels: list[str],
    record_origins: list[str],
    leave_types: list[str],
    credit_scopes: list[str],
    search_term: str,
    date_range: tuple[pd.Timestamp | None, pd.Timestamp | None],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Drop-in replacement for the old apply_filters(): identical signature
    and return shape, but results for a given (data, filter) combination are
    cached so repeated reruns with unchanged filters skip recomputation."""
    from form6_cache import _db_mtime_token

    mtime_token = _db_mtime_token()
    return _apply_filters_cached(
        employees,
        leaves,
        credits,
        mtime_token,
        tuple(grades or ()),
        tuple(employee_labels or ()),
        tuple(record_origins or ()),
        tuple(leave_types or ()),
        tuple(credit_scopes or ()),
        search_term or "",
        date_range,
    )
