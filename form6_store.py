from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
import re
import sqlite3
import sys
import uuid
from typing import Iterable

import pandas as pd

from form6_data import (
    compute_total_days,
    consolidate_workbooks,
    default_workbook_paths,
    grade_sort_key,
    month_from_date_value,
    month_sort_key,
    normalize_leave_type,
    normalize_month,
    normalize_service_credit,
    parse_single_date,
)


def _app_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


DB_PATH = _app_base_dir() / "form6_tracker.sqlite3"
SOURCE_DIR = _app_base_dir() / "data"


SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS employees (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    employee_key TEXT NOT NULL UNIQUE,
    grade TEXT NOT NULL,
    display_name TEXT NOT NULL,
    position TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS leave_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_key TEXT NOT NULL UNIQUE,
    employee_id INTEGER NOT NULL,
    date_of_filing TEXT,
    inclusive_dates TEXT NOT NULL DEFAULT '',
    month TEXT NOT NULL DEFAULT '',
    no_days REAL NOT NULL DEFAULT 0,
    no_halfdays REAL NOT NULL DEFAULT 0,
    leave_type TEXT NOT NULL DEFAULT '',
    service_credit_availed TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS service_credit_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_key TEXT NOT NULL UNIQUE,
    employee_id INTEGER NOT NULL,
    credit_scope TEXT NOT NULL DEFAULT '',
    event_date TEXT,
    service_attended TEXT NOT NULL DEFAULT '',
    credit_units REAL NOT NULL DEFAULT 0,
    source_kind TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS import_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    imported_at TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    source_name TEXT NOT NULL,
    employee_count INTEGER NOT NULL DEFAULT 0,
    leave_count INTEGER NOT NULL DEFAULT 0,
    credit_count INTEGER NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS app_settings (
    setting_key TEXT PRIMARY KEY,
    setting_value TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS attendance_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_key TEXT NOT NULL UNIQUE,
    employee_id INTEGER NOT NULL,
    record_date TEXT NOT NULL,
    status TEXT NOT NULL,
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

-- Reliever points: 1 point = 45 minutes of relieving duty, logged manually
-- per session. Every N points (N = app_settings 'reliever.points_per_credit',
-- default 8, i.e. 6 hours) automatically earns 1 LOCAL service credit, with
-- leftover points carried forward toward the next credit. See
-- form6_reliever.py for the conversion logic that reads this table and
-- writes the resulting service_credit_entries rows.
CREATE TABLE IF NOT EXISTS reliever_points_entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    record_key TEXT NOT NULL UNIQUE,
    employee_id INTEGER NOT NULL,
    entry_date TEXT NOT NULL DEFAULT '',
    points REAL NOT NULL DEFAULT 0,
    notes TEXT NOT NULL DEFAULT '',
    source_kind TEXT NOT NULL DEFAULT 'manual',
    source_ref TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(employee_id) REFERENCES employees(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_leave_employee_id ON leave_entries(employee_id);
CREATE INDEX IF NOT EXISTS idx_leave_date ON leave_entries(date_of_filing);
CREATE INDEX IF NOT EXISTS idx_credit_employee_id ON service_credit_entries(employee_id);
CREATE INDEX IF NOT EXISTS idx_credit_date ON service_credit_entries(event_date);
CREATE INDEX IF NOT EXISTS idx_import_log_imported_at ON import_log(imported_at);
CREATE INDEX IF NOT EXISTS idx_attendance_employee_id ON attendance_records(employee_id);
CREATE INDEX IF NOT EXISTS idx_attendance_date ON attendance_records(record_date);
CREATE INDEX IF NOT EXISTS idx_reliever_employee_id ON reliever_points_entries(employee_id);
CREATE INDEX IF NOT EXISTS idx_reliever_date ON reliever_points_entries(entry_date);
"""


@dataclass(frozen=True)
class Form6State:
    employees: pd.DataFrame
    leave_entries: pd.DataFrame
    service_credits: pd.DataFrame
    employee_summary: pd.DataFrame
    import_log: pd.DataFrame


@dataclass(frozen=True)
class ImportResult:
    source_name: str
    employee_count: int
    leave_count: int
    credit_count: int
    notes: str = ""


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def ensure_database() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


def now_iso() -> str:
    return datetime.now().replace(microsecond=0).isoformat(timespec="seconds")


def clean_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and pd.isna(value):
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    text = str(value).replace("\r", " ").replace("\n", " ").strip()
    return re.sub(r"\s+", " ", text)


def normalize_key(text: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "", clean_text(text).upper())


def build_employee_key(grade: str, display_name: str) -> str:
    return f"{normalize_key(grade)}|{normalize_key(display_name)}"


def build_display_name(last_name: str, first_name: str, middle_initial: str = "") -> str:
    last = clean_text(last_name).upper()
    first = clean_text(first_name).upper()
    middle = clean_text(middle_initial).upper()
    if last and first and middle:
        return f"{last}, {first} {middle}"
    if last and first:
        return f"{last}, {first}"
    return last or first or middle


def date_to_iso(value) -> str:
    parsed = parse_single_date(value)
    if pd.isna(parsed):
        return ""
    return pd.Timestamp(parsed).strftime("%Y-%m-%d")


def import_seed_workbooks(paths: Iterable[Path] | None = None) -> ImportResult:
    ensure_database()
    source_paths = list(paths or default_workbook_paths(SOURCE_DIR))
    if not source_paths:
        return ImportResult(source_name="seed", employee_count=0, leave_count=0, credit_count=0, notes="No source workbooks found.")

    consolidated = consolidate_workbooks([(path.name, path) for path in source_paths])

    employee_rows = consolidated.employee_summary.copy()
    leave_rows = consolidated.leave_entries.copy()
    credit_rows = consolidated.service_credits.copy()

    with connect() as conn:
        employee_id_map: dict[str, int] = {}

        for row in employee_rows.to_dict(orient="records"):
            grade = clean_text(row.get("grade"))
            display_name = clean_text(row.get("employee_name")) or clean_text(row.get("employee_sheet"))
            position = clean_text(row.get("position"))
            source_ref = f"{clean_text(row.get('source_file'))}::{clean_text(row.get('employee_sheet'))}"
            employee_id = upsert_employee(
                conn,
                grade=grade,
                display_name=display_name,
                position=position,
                source_kind="seed",
                source_ref=source_ref,
            )
            employee_id_map[build_employee_key(grade, display_name)] = employee_id

        for row in leave_rows.to_dict(orient="records"):
            grade = clean_text(row.get("grade"))
            display_name = clean_text(row.get("employee_name")) or clean_text(row.get("employee_sheet"))
            employee_id = employee_id_map.get(build_employee_key(grade, display_name))
            if employee_id is None:
                employee_id = upsert_employee(
                    conn,
                    grade=grade,
                    display_name=display_name,
                    position=clean_text(row.get("position")),
                    source_kind="seed",
                    source_ref=f"{clean_text(row.get('source_file'))}::{clean_text(row.get('source_sheet'))}",
                )
                employee_id_map[build_employee_key(grade, display_name)] = employee_id

            insert_leave_entry(
                conn,
                employee_id=employee_id,
                date_of_filing=date_to_iso(row.get("date_of_filing")),
                inclusive_dates=clean_text(row.get("inclusive_dates")),
                month=normalize_month(row.get("month")),
                no_days=float(row.get("no_days") or 0),
                no_halfdays=float(row.get("no_halfdays") or 0),
                leave_type=normalize_leave_type(row.get("leave_type")),
                service_credit_availed=normalize_service_credit(row.get("service_credit_availed")),
                source_kind="seed",
                source_ref=f"{clean_text(row.get('source_file'))}::{clean_text(row.get('source_sheet'))}:{row.get('source_row')}",
                record_key=f"seed:leave:{clean_text(row.get('source_file'))}:{clean_text(row.get('source_sheet'))}:{row.get('source_row')}",
            )

        for row in credit_rows.to_dict(orient="records"):
            grade = clean_text(row.get("grade"))
            display_name = clean_text(row.get("employee_name")) or clean_text(row.get("employee_sheet"))
            employee_id = employee_id_map.get(build_employee_key(grade, display_name))
            if employee_id is None:
                employee_id = upsert_employee(
                    conn,
                    grade=grade,
                    display_name=display_name,
                    position="",
                    source_kind="seed",
                    source_ref=f"{clean_text(row.get('source_file'))}::{clean_text(row.get('source_sheet'))}",
                )
                employee_id_map[build_employee_key(grade, display_name)] = employee_id

            insert_service_credit_entry(
                conn,
                employee_id=employee_id,
                credit_scope=normalize_service_credit(row.get("credit_scope")),
                event_date=date_to_iso(row.get("event_date")),
                service_attended=clean_text(row.get("service_attended")),
                credit_units=float(row.get("credit_units") or 0),
                source_kind="seed",
                source_ref=f"{clean_text(row.get('source_file'))}::{clean_text(row.get('source_sheet'))}:{row.get('source_row')}:{normalize_service_credit(row.get('credit_scope'))}",
                record_key=(
                    f"seed:credit:{clean_text(row.get('source_file'))}:"
                    f"{clean_text(row.get('source_sheet'))}:{row.get('source_row')}:"
                    f"{normalize_service_credit(row.get('credit_scope'))}"
                ),
            )

        import_log_entry(
            conn,
            source_kind="seed",
            source_name=f"{len(source_paths)} workbook(s)",
            employee_count=len(employee_rows),
            leave_count=len(leave_rows),
            credit_count=len(credit_rows),
            notes="Seeded from bundled workbook files.",
        )

        return ImportResult(
            source_name=f"{len(source_paths)} workbook(s)",
            employee_count=len(employee_rows),
            leave_count=len(leave_rows),
            credit_count=len(credit_rows),
            notes="Seeded from bundled workbook files.",
        )


def upsert_employee(
    conn: sqlite3.Connection,
    *,
    grade: str,
    display_name: str,
    position: str = "",
    source_kind: str = "manual",
    source_ref: str = "",
) -> int:
    ensure_database()
    employee_key = build_employee_key(grade, display_name)
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO employees (
            employee_key, grade, display_name, position, source_kind, source_ref, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(employee_key) DO UPDATE SET
            grade = excluded.grade,
            display_name = excluded.display_name,
            position = CASE
                WHEN excluded.position IS NOT NULL AND excluded.position != '' THEN excluded.position
                ELSE employees.position
            END,
            source_ref = CASE
                WHEN excluded.source_ref IS NOT NULL AND excluded.source_ref != '' THEN excluded.source_ref
                ELSE employees.source_ref
            END,
            updated_at = excluded.updated_at
        """,
        (employee_key, clean_text(grade), clean_text(display_name), clean_text(position), source_kind, clean_text(source_ref), ts, ts),
    )
    row = conn.execute("SELECT id FROM employees WHERE employee_key = ?", (employee_key,)).fetchone()
    if row is None:
        raise RuntimeError("Failed to fetch employee after upsert.")
    return int(row["id"])


def insert_leave_entry(
    conn: sqlite3.Connection,
    *,
    employee_id: int,
    date_of_filing: str = "",
    inclusive_dates: str = "",
    month: str = "",
    no_days: float = 0,
    no_halfdays: float = 0,
    leave_type: str = "",
    service_credit_availed: str = "",
    source_kind: str = "manual",
    source_ref: str = "",
    record_key: str | None = None,
) -> str:
    ensure_database()
    filing = clean_text(date_of_filing)
    days = float(no_days or 0)
    half_days = float(no_halfdays or 0)
    if compute_total_days(days, half_days) <= 0:
        raise ValueError("Leave must have at least 0.5 day (whole or half-day).")

    # Month always follows the filing date when one is present — avoids
    # mismatched month/date pairs that break reports and filters.
    month_norm = month_from_date_value(filing) if filing else normalize_month(month)

    key = record_key or f"leave:{uuid.uuid4().hex}"
    ts = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO leave_entries (
            record_key, employee_id, date_of_filing, inclusive_dates, month,
            no_days, no_halfdays, leave_type, service_credit_availed,
            source_kind, source_ref, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            employee_id,
            filing,
            clean_text(inclusive_dates),
            month_norm,
            days,
            half_days,
            normalize_leave_type(leave_type),
            normalize_service_credit(service_credit_availed),
            source_kind,
            clean_text(source_ref),
            ts,
            ts,
        ),
    )
    return key


def insert_service_credit_entry(
    conn: sqlite3.Connection,
    *,
    employee_id: int,
    credit_scope: str = "",
    event_date: str = "",
    service_attended: str = "",
    credit_units: float = 0,
    source_kind: str = "manual",
    source_ref: str = "",
    record_key: str | None = None,
) -> str:
    ensure_database()
    units = float(credit_units or 0)
    if units <= 0:
        raise ValueError("Service credit units must be greater than zero.")

    scope = normalize_service_credit(credit_scope)
    if scope not in {"LOCAL", "DIVISION"}:
        raise ValueError("Credit scope must be LOCAL or DIVISION.")

    key = record_key or f"credit:{uuid.uuid4().hex}"
    ts = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO service_credit_entries (
            record_key, employee_id, credit_scope, event_date, service_attended, credit_units,
            source_kind, source_ref, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            employee_id,
            scope,
            clean_text(event_date),
            clean_text(service_attended),
            units,
            source_kind,
            clean_text(source_ref),
            ts,
            ts,
        ),
    )
    return key


def insert_reliever_points_entry(
    conn: sqlite3.Connection,
    *,
    employee_id: int,
    points: float,
    entry_date: str = "",
    notes: str = "",
    source_kind: str = "manual",
    source_ref: str = "",
    record_key: str | None = None,
) -> str:
    """Log one reliever session in points (1 point = 45 minutes; see
    form6_reliever.py for the minutes-per-point setting and the
    points -> LOCAL service credit conversion that runs after this insert)."""
    ensure_database()
    points_value = float(points or 0)
    if points_value <= 0:
        raise ValueError("Reliever points must be greater than zero.")

    key = record_key or f"reliever:{uuid.uuid4().hex}"
    ts = now_iso()
    conn.execute(
        """
        INSERT OR IGNORE INTO reliever_points_entries (
            record_key, employee_id, entry_date, points, notes,
            source_kind, source_ref, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            key,
            employee_id,
            clean_text(entry_date),
            points_value,
            clean_text(notes),
            source_kind,
            clean_text(source_ref),
            ts,
            ts,
        ),
    )
    return key


def load_reliever_points() -> pd.DataFrame:
    """Load every reliever_points_entries row, joined with employee_label
    for display, as a flat DataFrame. Mirrors load_attendance_records()'s
    shape so it's familiar to work with."""
    ensure_database()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT r.id, r.record_key, r.employee_id, r.entry_date, r.points, r.notes,
                   r.source_kind, r.source_ref, r.created_at, r.updated_at,
                   e.grade, e.display_name, e.position
            FROM reliever_points_entries r
            JOIN employees e ON e.id = r.employee_id
            ORDER BY e.grade, e.display_name, r.entry_date, r.id
            """
        ).fetchall()
    df = pd.DataFrame([dict(row) for row in rows])
    if df.empty:
        return pd.DataFrame(columns=[
            "id", "record_key", "employee_id", "entry_date", "points", "notes",
            "source_kind", "source_ref", "created_at", "updated_at",
            "grade", "display_name", "position", "employee_label",
        ])
    df["employee_label"] = df["grade"].fillna("") + " | " + df["display_name"].fillna("")
    return df


def import_log_entry(
    conn: sqlite3.Connection,
    *,
    source_kind: str,
    source_name: str,
    employee_count: int,
    leave_count: int,
    credit_count: int,
    notes: str = "",
) -> None:
    ensure_database()
    conn.execute(
        """
        INSERT INTO import_log (
            imported_at, source_kind, source_name, employee_count, leave_count, credit_count, notes
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (now_iso(), source_kind, source_name, int(employee_count), int(leave_count), int(credit_count), clean_text(notes)),
    )


def load_state() -> Form6State:
    ensure_database()
    with connect() as conn:
        employees = pd.read_sql_query(
            """
            SELECT id, employee_key, grade, display_name, position, source_kind, source_ref, created_at, updated_at
            FROM employees
            ORDER BY grade, display_name
            """,
            conn,
            parse_dates=["created_at", "updated_at"],
        )
        leave_entries = pd.read_sql_query(
            """
            SELECT
                l.id, l.record_key, l.employee_id, e.grade, e.display_name AS employee_name, e.position,
                l.date_of_filing, l.inclusive_dates, l.month, l.no_days, l.no_halfdays,
                (l.no_days + l.no_halfdays) AS total_days,
                l.leave_type, l.service_credit_availed, l.source_kind, l.source_ref,
                l.created_at, l.updated_at
            FROM leave_entries l
            JOIN employees e ON e.id = l.employee_id
            ORDER BY e.grade, e.display_name, l.date_of_filing, l.id
            """,
            conn,
            parse_dates=["date_of_filing", "created_at", "updated_at"],
        )
        service_credits = pd.read_sql_query(
            """
            SELECT
                c.id, c.record_key, c.employee_id, e.grade, e.display_name AS employee_name, e.position,
                c.credit_scope, c.event_date, c.service_attended, c.credit_units,
                c.source_kind, c.source_ref, c.created_at, c.updated_at
            FROM service_credit_entries c
            JOIN employees e ON e.id = c.employee_id
            ORDER BY e.grade, e.display_name, c.id
            """,
            conn,
            parse_dates=["created_at", "updated_at"],
        )
        import_log_df = pd.read_sql_query(
            """
            SELECT id, imported_at, source_kind, source_name, employee_count, leave_count, credit_count, notes
            FROM import_log
            ORDER BY imported_at DESC, id DESC
            """,
            conn,
            parse_dates=["imported_at"],
        )

    if not employees.empty:
        employees = employees.copy()
        employees["employee_label"] = employees["grade"].astype(str) + " | " + employees["display_name"].astype(str)
        employees["_grade_sort"] = employees["grade"].map(grade_sort_key)
        employees = employees.sort_values(["_grade_sort", "display_name"]).drop(columns=["_grade_sort"]).reset_index(drop=True)
    else:
        employees = employees.copy()
        employees["employee_label"] = pd.Series(dtype=str)

    if not leave_entries.empty:
        leave_entries = leave_entries.copy()
        leave_entries["employee_label"] = leave_entries["grade"].astype(str) + " | " + leave_entries["employee_name"].astype(str)
        leave_entries["month"] = leave_entries["month"].fillna("").astype(str).str.upper()
        leave_entries["leave_type"] = leave_entries["leave_type"].fillna("").astype(str).str.upper()
        leave_entries["service_credit_availed"] = leave_entries["service_credit_availed"].fillna("").astype(str).str.upper()
        leave_entries["_grade_sort"] = leave_entries["grade"].map(grade_sort_key)
        leave_entries["_month_sort"] = leave_entries["month"].map(month_sort_key)
        leave_entries = leave_entries.sort_values(
            ["_grade_sort", "employee_name", "date_of_filing", "id"], na_position="last"
        ).drop(columns=["_grade_sort", "_month_sort"]).reset_index(drop=True)
    else:
        leave_entries = leave_entries.copy()
        leave_entries["employee_label"] = pd.Series(dtype=str)

    if not service_credits.empty:
        service_credits = service_credits.copy()
        service_credits["employee_label"] = service_credits["grade"].astype(str) + " | " + service_credits["employee_name"].astype(str)
        service_credits["credit_scope"] = service_credits["credit_scope"].fillna("").astype(str).str.upper()
        service_credits["_grade_sort"] = service_credits["grade"].map(grade_sort_key)
        service_credits = service_credits.sort_values(
            ["_grade_sort", "employee_name", "event_date", "id"], na_position="last"
        ).drop(columns=["_grade_sort"]).reset_index(drop=True)
    else:
        service_credits = service_credits.copy()
        service_credits["employee_label"] = pd.Series(dtype=str)

    employee_summary = build_employee_summary(employees, leave_entries, service_credits)
    return Form6State(
        employees=employees,
        leave_entries=leave_entries,
        service_credits=service_credits,
        employee_summary=employee_summary,
        import_log=import_log_df,
    )


def build_employee_summary(
    employees: pd.DataFrame,
    leave_entries: pd.DataFrame,
    service_credits: pd.DataFrame,
) -> pd.DataFrame:
    if employees.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "employee_key",
                "grade",
                "display_name",
                "employee_label",
                "position",
                "leave_entries",
                "leave_days",
                "personal_leave",
                "sick_leave",
                "vacation_leave",
                "special_privilege_leave",
                "other_leave",
                "local_earned",
                "local_used",
                "local_balance",
                "national_earned",
                "national_used",
                "national_balance",
                "last_filing_date",
                "source_kind",
                "source_ref",
            ]
        )

    summary = employees[
        ["id", "employee_key", "grade", "display_name", "employee_label", "position", "source_kind", "source_ref"]
    ].copy()

    if not leave_entries.empty:
        leaves = leave_entries.copy()
        leaves["total_days"] = pd.to_numeric(leaves["total_days"], errors="coerce").fillna(0)
        leaves["leave_type_norm"] = leaves["leave_type"].fillna("").astype(str).str.upper()
        leaves["scope_norm"] = leaves["service_credit_availed"].fillna("").astype(str).str.upper()

        agg = (
            leaves.groupby("employee_id")
            .agg(
                leave_entries=("id", "count"),
                leave_days=("total_days", "sum"),
                last_filing_date=("date_of_filing", "max"),
            )
            .reset_index()
        )

        type_counts = (
            leaves.pivot_table(index="employee_id", columns="leave_type_norm", values="id", aggfunc="count", fill_value=0)
            .reset_index()
        )
        expected_types = [
            "PERSONAL LEAVE",
            "SICK LEAVE",
            "VACATION LEAVE",
            "SPECIAL PRIVILEGE LEAVE",
            "OTHER LEAVE",
        ]
        for value in expected_types:
            if value not in type_counts.columns:
                type_counts[value] = 0
        type_counts = type_counts[["employee_id"] + expected_types].rename(
            columns={
                "PERSONAL LEAVE": "personal_leave",
                "SICK LEAVE": "sick_leave",
                "VACATION LEAVE": "vacation_leave",
                "SPECIAL PRIVILEGE LEAVE": "special_privilege_leave",
                "OTHER LEAVE": "other_leave",
            }
        )

        used_local = (
            leaves.loc[leaves["scope_norm"] == "LOCAL"]
            .groupby("employee_id")["total_days"]
            .sum()
            .rename("local_used")
            .reset_index()
        )
        used_national = (
            leaves.loc[leaves["scope_norm"] == "DIVISION"]
            .groupby("employee_id")["total_days"]
            .sum()
            .rename("national_used")
            .reset_index()
        )

        summary = summary.merge(agg, left_on="id", right_on="employee_id", how="left").drop(columns=["employee_id"])
        summary = summary.merge(type_counts, left_on="id", right_on="employee_id", how="left").drop(columns=["employee_id"])
        summary = summary.merge(used_local, left_on="id", right_on="employee_id", how="left").drop(columns=["employee_id"])
        summary = summary.merge(used_national, left_on="id", right_on="employee_id", how="left").drop(columns=["employee_id"])
    else:
        summary["leave_entries"] = 0
        summary["leave_days"] = 0.0
        summary["personal_leave"] = 0
        summary["sick_leave"] = 0
        summary["vacation_leave"] = 0
        summary["special_privilege_leave"] = 0
        summary["other_leave"] = 0
        summary["local_used"] = 0.0
        summary["national_used"] = 0.0
        summary["last_filing_date"] = pd.NaT

    if not service_credits.empty:
        credits = service_credits.copy()
        credits["credit_scope_norm"] = credits["credit_scope"].fillna("").astype(str).str.upper()
        credits["credit_units"] = pd.to_numeric(credits["credit_units"], errors="coerce").fillna(0)
        earned = (
            credits.pivot_table(index="employee_id", columns="credit_scope_norm", values="credit_units", aggfunc="sum", fill_value=0)
            .reset_index()
        )
        for value in ["LOCAL", "DIVISION"]:
            if value not in earned.columns:
                earned[value] = 0.0
        earned = earned[["employee_id", "LOCAL", "DIVISION"]].rename(
            columns={"LOCAL": "local_earned", "DIVISION": "national_earned"}
        )
        summary = summary.merge(earned, left_on="id", right_on="employee_id", how="left").drop(columns=["employee_id"])
    else:
        summary["local_earned"] = 0.0
        summary["national_earned"] = 0.0

    for column in [
        "leave_entries",
        "leave_days",
        "personal_leave",
        "sick_leave",
        "vacation_leave",
        "special_privilege_leave",
        "other_leave",
        "local_earned",
        "local_used",
        "national_earned",
        "national_used",
    ]:
        summary[column] = pd.to_numeric(summary[column], errors="coerce").fillna(0)

    summary["local_balance"] = summary["local_earned"] - summary["local_used"]
    summary["national_balance"] = summary["national_earned"] - summary["national_used"]
    summary["employee_label"] = summary["grade"].astype(str) + " | " + summary["display_name"].astype(str)
    summary["last_filing_date"] = pd.to_datetime(summary.get("last_filing_date"), errors="coerce")
    summary["_grade_sort"] = summary["grade"].map(grade_sort_key)
    summary = summary.sort_values(["_grade_sort", "display_name"], na_position="last").drop(columns=["_grade_sort"])
    return summary.reset_index(drop=True)


def db_counts() -> dict[str, int]:
    ensure_database()
    with connect() as conn:
        counts = {
            "employees": conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0],
            "leave_entries": conn.execute("SELECT COUNT(*) FROM leave_entries").fetchone()[0],
            "service_credits": conn.execute("SELECT COUNT(*) FROM service_credit_entries").fetchone()[0],
            "attendance_records": conn.execute("SELECT COUNT(*) FROM attendance_records").fetchone()[0],
            "reliever_points": conn.execute("SELECT COUNT(*) FROM reliever_points_entries").fetchone()[0],
            "imports": conn.execute("SELECT COUNT(*) FROM import_log").fetchone()[0],
        }
    return {key: int(value) for key, value in counts.items()}


def database_exists() -> bool:
    return DB_PATH.exists()


def insert_attendance_record(
    conn: sqlite3.Connection,
    *,
    employee_id: int,
    record_date: str,
    status: str,
    source_ref: str = "",
) -> str:
    """Insert one day's raw attendance status (PRESENT/LATE/ABSENT) for one
    employee. The record_key is deterministic from employee_id + date (not a
    random UUID like leave/credit entries), so re-importing the same month's
    file twice is naturally idempotent via INSERT OR IGNORE rather than
    creating duplicate rows."""
    ensure_database()
    record_date_iso = clean_text(record_date)
    key = f"attendance:{employee_id}:{record_date_iso}"
    conn.execute(
        """
        INSERT OR IGNORE INTO attendance_records (
            record_key, employee_id, record_date, status, source_ref, created_at
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (key, employee_id, record_date_iso, clean_text(status).upper(), clean_text(source_ref), now_iso()),
    )
    return key


def load_attendance_records() -> pd.DataFrame:
    """Load every attendance_records row, joined with employee_label for
    display, as a flat DataFrame. Mirrors the shape of load_state()'s other
    frames so it's familiar to work with."""
    ensure_database()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT a.id, a.employee_id, a.record_date, a.status, a.source_ref,
                   e.grade, e.display_name, e.position
            FROM attendance_records a
            JOIN employees e ON e.id = a.employee_id
            """
        ).fetchall()
    df = pd.DataFrame([dict(row) for row in rows])
    if df.empty:
        return pd.DataFrame(columns=["id", "employee_id", "record_date", "status", "source_ref", "grade", "display_name", "position", "employee_label"])
    df["employee_label"] = df["grade"].fillna("") + " | " + df["display_name"].fillna("")
    return df
