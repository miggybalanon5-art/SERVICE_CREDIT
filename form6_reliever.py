"""
Reliever points: manually-logged relieving duty, converted automatically
into LOCAL service credits.

Conversion rule (configurable in form6_settings, defaults shown):
    1 point          = 45 minutes  (SETTING_RELIEVER_MINUTES_PER_POINT)
    8 points (6 hrs) = 1 LOCAL service credit (SETTING_RELIEVER_POINTS_PER_CREDIT)

Leftover points below the next threshold are NOT discarded — they carry
over and count toward the next credit. This is implemented by always
recomputing from the employee's full cumulative point history rather than
maintaining a separate "points remaining" counter, so there's nothing that
can drift out of sync between runs:

    total_credits_due = floor(total_points_logged / points_per_credit)

Each time a new point entry is saved, we compare total_credits_due against
how many reliever-sourced credits already exist for that employee and
create only the newly-earned difference. This makes the conversion
idempotent and safe to re-run (e.g. if it's ever re-applied after a bulk
data fix) without ever double-issuing a credit.

Reliever-issued credits are tagged source_kind='reliever' in
service_credit_entries specifically so this module can tell them apart
from manually-encoded or seeded credits when counting "how many credits
have already been issued from points."
"""

from __future__ import annotations

import math
import sqlite3

import pandas as pd

from form6_settings import get_reliever_settings
from form6_store import clean_text, insert_service_credit_entry, now_iso

RELIEVER_CREDIT_SOURCE_KIND = "reliever"
RELIEVER_CREDIT_SCOPE = "LOCAL"


def points_to_hours(points: float, minutes_per_point: float | None = None) -> float:
    """Convenience display helper: convert a point total to hours."""
    if minutes_per_point is None:
        minutes_per_point = get_reliever_settings()["minutes_per_point"]
    return float(points or 0) * float(minutes_per_point) / 60.0


def _total_points_for_employee(conn: sqlite3.Connection, employee_id: int) -> float:
    row = conn.execute(
        "SELECT COALESCE(SUM(points), 0) AS total FROM reliever_points_entries WHERE employee_id = ?",
        (employee_id,),
    ).fetchone()
    return float(row["total"] if row is not None else 0) or 0.0


def _reliever_credits_already_issued(conn: sqlite3.Connection, employee_id: int) -> int:
    """Count how many LOCAL credits have already been auto-issued from
    reliever points for this employee. Counts rows, not summed units,
    since this module always issues exactly 1.0 unit per credit — counting
    rows is robust even if someone manually edits a unit value later."""
    row = conn.execute(
        """
        SELECT COUNT(*) AS n FROM service_credit_entries
        WHERE employee_id = ? AND source_kind = ? AND credit_scope = ?
        """,
        (employee_id, RELIEVER_CREDIT_SOURCE_KIND, RELIEVER_CREDIT_SCOPE),
    ).fetchone()
    return int(row["n"] if row is not None else 0) or 0


def reliever_progress_for_employee(employee_id: int, conn: sqlite3.Connection | None = None) -> dict:
    """Returns the current reliever-points standing for one employee:
    total points ever logged, points already converted, points still
    carrying over toward the next credit, and credits earned so far.
    Read-only — does not write anything."""
    settings = get_reliever_settings()
    points_per_credit = settings["points_per_credit"]

    owns_conn = conn is None
    if owns_conn:
        from form6_store import connect
        conn = connect()
    try:
        total_points = _total_points_for_employee(conn, employee_id)
        credits_issued = _reliever_credits_already_issued(conn, employee_id)
    finally:
        if owns_conn:
            conn.close()

    total_credits_due = math.floor(total_points / points_per_credit) if points_per_credit > 0 else 0
    points_carried_over = total_points - (total_credits_due * points_per_credit)
    # Carryover is the remainder after all credits due from total points,
    # even if an already-issued credit row was later deleted manually.
    points_carried_over = max(0.0, points_carried_over)

    return {
        "total_points": total_points,
        "credits_issued": credits_issued,
        "total_credits_due": total_credits_due,
        "credits_pending": max(0, total_credits_due - credits_issued),
        "points_carried_over": points_carried_over,
        "points_per_credit": points_per_credit,
        "minutes_per_point": settings["minutes_per_point"],
    }


def log_reliever_points_and_convert(
    conn: sqlite3.Connection,
    *,
    employee_id: int,
    points: float,
    entry_date: str = "",
    notes: str = "",
    source_kind: str = "manual",
    source_ref: str = "",
) -> dict:
    """Insert one reliever points entry, then issue any newly-earned LOCAL
    service credit(s) on the same connection/transaction so the points log
    and the resulting credit(s) are always saved together.

    Returns a dict describing what happened, so the caller can show an
    accurate confirmation message:
        {
            "points_logged": float,
            "credits_newly_issued": int,
            "total_points": float,
            "points_carried_over": float,
        }
    """
    from form6_store import insert_reliever_points_entry

    insert_reliever_points_entry(
        conn,
        employee_id=employee_id,
        points=points,
        entry_date=entry_date,
        notes=notes,
        source_kind=source_kind,
        source_ref=source_ref,
    )

    settings = get_reliever_settings()
    points_per_credit = settings["points_per_credit"]

    total_points = _total_points_for_employee(conn, employee_id)
    credits_already_issued = _reliever_credits_already_issued(conn, employee_id)
    total_credits_due = math.floor(total_points / points_per_credit) if points_per_credit > 0 else 0
    credits_to_issue = max(0, total_credits_due - credits_already_issued)

    for _ in range(credits_to_issue):
        insert_service_credit_entry(
            conn,
            employee_id=employee_id,
            credit_scope=RELIEVER_CREDIT_SCOPE,
            event_date=clean_text(entry_date) or now_iso()[:10],
            service_attended=f"Reliever duty ({points_per_credit:.0f} pts = {points_to_hours(points_per_credit, settings['minutes_per_point']):.1f} hrs)",
            credit_units=1.0,
            source_kind=RELIEVER_CREDIT_SOURCE_KIND,
            source_ref=source_ref or "reliever-auto-conversion",
        )

    points_carried_over = max(0.0, total_points - (total_credits_due * points_per_credit))

    return {
        "points_logged": float(points),
        "credits_newly_issued": credits_to_issue,
        "total_points": total_points,
        "points_carried_over": points_carried_over,
    }


def load_reliever_summary(employees: pd.DataFrame) -> pd.DataFrame:
    """Build a per-employee reliever points summary DataFrame for display:
    total points logged, credits issued, and points carried over toward
    the next credit. Mirrors the shape of build_employee_summary() closely
    enough to slot into the same kind of table/ledger UI."""
    if employees.empty:
        return pd.DataFrame(columns=[
            "id", "employee_label", "total_points", "total_hours",
            "credits_issued", "points_carried_over",
        ])

    from form6_store import connect

    rows = []
    with connect() as conn:
        settings = get_reliever_settings()
        for _, emp in employees.iterrows():
            employee_id = int(emp["id"])
            progress = reliever_progress_for_employee(employee_id, conn=conn)
            rows.append({
                "id": employee_id,
                "employee_label": emp.get("employee_label", ""),
                "total_points": progress["total_points"],
                "total_hours": points_to_hours(progress["total_points"], settings["minutes_per_point"]),
                "credits_issued": progress["credits_issued"],
                "points_carried_over": progress["points_carried_over"],
            })

    return pd.DataFrame(rows)
