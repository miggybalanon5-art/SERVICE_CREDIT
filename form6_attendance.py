"""
Attendance Summary: per-employee, per-month counts of Present / Late /
Absent / On Leave days.

Data sources:
  - attendance_records: the raw daily biometrics fact (PRESENT/LATE/ABSENT)
    for every day in an imported month, written by form6_biometrics.commit_import().
  - leave_entries: approved leave, used to compute "On Leave" day counts.
    This is also how a day where biometrics says ABSENT but the employee was
    actually on approved leave gets correctly counted as "On Leave" instead
    of "Absent" - see _expand_leave_days_in_month() below.

IMPORTANT: a day is counted as "On Leave" based on leave_entries, not based
on whatever label the biometrics file used. This was a deliberate choice -
the leave records are the actual source of truth for approved leave, and a
biometrics device's guess (or lack of any signal at all) shouldn't override
that. See form6_biometrics.py's conflict-detection for the related logic.
"""

from __future__ import annotations

import calendar

import pandas as pd
from dateutil import parser as dateparser

from form6_biometrics import parse_inclusive_date_range

STATUS_PRESENT = "PRESENT"
STATUS_LATE = "LATE"
STATUS_ABSENT = "ABSENT"


def _expand_leave_days_in_month(leave_all: pd.DataFrame, employee_id: int, year: int, month: int) -> set:
    """Returns the set of calendar dates (as date objects) within the given
    month that this employee was on approved leave, based on date_of_filing
    and any parseable inclusive_dates range. Excludes biometrics-sourced
    rows (those are deductions derived FROM attendance, not the leave
    records that attendance should be checked against)."""
    if leave_all.empty:
        return set()

    month_start = pd.Timestamp(year=year, month=month, day=1).date()
    days_in_month = calendar.monthrange(year, month)[1]
    month_end = pd.Timestamp(year=year, month=month, day=days_in_month).date()

    employee_leaves = leave_all[
        (leave_all["employee_id"] == employee_id) & (leave_all.get("source_kind", "") != "biometrics")
    ]

    leave_days: set = set()
    for _, row in employee_leaves.iterrows():
        start, end = parse_inclusive_date_range(row.get("inclusive_dates", ""))
        if start is None or end is None:
            # Fall back to the single filing date if inclusive_dates couldn't
            # be parsed - better to count one day than none at all.
            filing = str(row.get("date_of_filing", "") or "").strip()
            if not filing:
                continue
            try:
                parsed = dateparser.parse(filing).date()
                start = end = parsed
            except (ValueError, OverflowError):
                continue

        # Clip the leave range to this month before iterating, so a leave
        # spanning a month boundary doesn't loop over irrelevant days.
        clipped_start = max(start, month_start)
        clipped_end = min(end, month_end)
        current = clipped_start
        while current <= clipped_end:
            leave_days.add(current)
            current += pd.Timedelta(days=1)

    return leave_days


def build_attendance_calendar_days(
    attendance_all: pd.DataFrame,
    leave_all: pd.DataFrame,
    employee_id: int,
    year: int,
    month: int,
) -> dict:
    """Returns a {date: status} map for every day in the given month that
    has a determinable status for one employee, where status is one of
    "PRESENT", "LATE", "ABSENT", or "ON_LEAVE".

    Reuses _expand_leave_days_in_month() directly so a calendar built from
    this function can never disagree with build_attendance_summary()'s
    monthly counts for the same employee/month - same priority rule
    (On Leave overrides a biometrics ABSENT reading), same leave-matching.

    Days with no attendance record and no approved leave are simply absent
    from the returned dict (the caller decides how to render an "unknown"
    day - e.g. weekends, days before the employee was hired, or days the
    school was closed, none of which this function can distinguish from
    "biometrics data hasn't been imported yet").
    """
    on_leave_dates = _expand_leave_days_in_month(leave_all, employee_id, year, month)

    day_status: dict = {}
    for leave_date in on_leave_dates:
        day_status[leave_date] = "ON_LEAVE"

    if not attendance_all.empty:
        record_dates = pd.to_datetime(attendance_all["record_date"], errors="coerce")
        month_mask = (record_dates.dt.year == year) & (record_dates.dt.month == month)
        employee_mask = attendance_all["employee_id"] == employee_id
        employee_month_attendance = attendance_all[month_mask & employee_mask]

        for _, row in employee_month_attendance.iterrows():
            row_date = pd.to_datetime(row["record_date"], errors="coerce")
            if pd.isna(row_date):
                continue
            row_date = row_date.date()
            if row_date in day_status:
                # Already ON_LEAVE - On Leave takes priority over whatever
                # the biometrics device recorded, same rule as the monthly
                # summary above.
                continue
            status = str(row.get("status", "")).strip().upper()
            if status in (STATUS_PRESENT, STATUS_LATE, STATUS_ABSENT):
                day_status[row_date] = status

    return day_status


def build_attendance_summary(
    attendance_all: pd.DataFrame,
    leave_all: pd.DataFrame,
    employees: pd.DataFrame,
    year: int,
    month: int,
) -> pd.DataFrame:
    """Returns one row per employee for the given month, with columns:
    employee_id, employee_label, grade, present_days, late_days, absent_days,
    on_leave_days, total_recorded_days.

    "Absent" here counts only days where the attendance record says ABSENT
    AND the day is not also covered by an approved leave (a day can't be
    both - On Leave takes priority, matching the conflict-detection logic
    used during import).
    """
    if employees.empty:
        return pd.DataFrame(columns=[
            "employee_id", "employee_label", "grade", "present_days",
            "late_days", "absent_days", "on_leave_days", "total_recorded_days",
        ])

    month_attendance = pd.DataFrame()
    if not attendance_all.empty:
        record_dates = pd.to_datetime(attendance_all["record_date"], errors="coerce")
        month_mask = (record_dates.dt.year == year) & (record_dates.dt.month == month)
        month_attendance = attendance_all[month_mask].copy()

    rows = []
    for _, employee in employees.iterrows():
        employee_id = int(employee["id"])
        employee_label = str(employee.get("employee_label", ""))
        grade = str(employee.get("grade", ""))

        on_leave_dates = _expand_leave_days_in_month(leave_all, employee_id, year, month)

        if month_attendance.empty:
            present_days = late_days = absent_days = 0
        else:
            employee_attendance = month_attendance[month_attendance["employee_id"] == employee_id]
            attendance_dates = pd.to_datetime(employee_attendance["record_date"], errors="coerce").dt.date
            status_values = employee_attendance["status"].fillna("").astype(str).str.upper()

            present_days = int((status_values == STATUS_PRESENT).sum())
            late_days = int((status_values == STATUS_LATE).sum())

            # Absent days exclude any date that's also in on_leave_dates -
            # On Leave takes priority over a biometrics ABSENT reading.
            absent_mask = (status_values == STATUS_ABSENT) & (~attendance_dates.isin(on_leave_dates))
            absent_days = int(absent_mask.sum())

        # On Leave days within the month, counted from leave_entries directly
        # (not dependent on a matching attendance record existing at all -
        # the normal case is no punch, no row, for a day on approved leave).
        on_leave_days = len(on_leave_dates)

        rows.append({
            "employee_id": employee_id,
            "employee_label": employee_label,
            "grade": grade,
            "present_days": present_days,
            "late_days": late_days,
            "absent_days": absent_days,
            "on_leave_days": on_leave_days,
            "total_recorded_days": present_days + late_days + absent_days + on_leave_days,
        })

    return pd.DataFrame(rows)
