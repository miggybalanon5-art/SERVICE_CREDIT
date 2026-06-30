"""
PROTOTYPE: Biometrics import - deduct lates/absents from LOCAL/DIVISION credit.

STATUS: placeholder rules, not final. Three things are very likely to change
once the real biometrics export format and deduction policy are decided:

  1. EXPECTED_COLUMNS / read_biometrics_excel() - the actual column names,
     date format, and one-row-per-what shape of the real Excel file.
  2. DEDUCTION RULES (just below) - currently a flat placeholder.
  3. Employee matching - currently exact-normalized-name match only, with
     unmatched rows surfaced rather than silently dropped or guessed at.

Everything else in the app (balance math, Leave Ledger, summary) requires NO
changes: each deduction is stored as an ordinary leave_entries row with
leave_type="ABSENT" / "LATE" and service_credit_availed set to the chosen
scope, reusing insert_leave_entry() exactly as manual leave entries do. This
also means deductions show up in the existing Leave Ledger automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date

import pandas as pd
from dateutil import parser as dateparser

from form6_settings import DEFAULT_ABSENT_DEDUCTION_DAYS, DEFAULT_LATE_DEDUCTION_DAYS
from form6_store import clean_text, date_to_iso, insert_attendance_record, insert_leave_entry, normalize_key

# ----------------------------------------------------------------------------
# 1. DEDUCTION RULES - now configurable in-app (see form6_settings.py and the
#    "Biometrics Settings" section of the Import/Backup tab). The values
#    below are only the fallback defaults used if nothing has been saved yet.
# ----------------------------------------------------------------------------
LEAVE_TYPE_ABSENT = "ABSENT"
LEAVE_TYPE_LATE = "LATE"

# ----------------------------------------------------------------------------
# 2. PLACEHOLDER EXCEL SHAPE - replace once a real sample file is available
# ----------------------------------------------------------------------------
EXPECTED_COLUMNS = {
    "name": ["Employee Name", "Name", "Employee"],
    "date": ["Date"],
    "status": ["Status", "Remarks", "Type"],
    "minutes_late": ["Minutes Late", "Late (mins)"],
}


@dataclass
class BiometricsRow:
    raw_name: str
    matched_employee_id: int | None
    matched_label: str | None
    date_iso: str
    status: str
    deduction_days: float  # 0.0 for PRESENT rows - they're not a deduction


@dataclass
class BiometricsImportPreview:
    matched: list[BiometricsRow] = field(default_factory=list)
    unmatched: list[BiometricsRow] = field(default_factory=list)
    conflicts: list[BiometricsRow] = field(default_factory=list)
    present_rows: list[BiometricsRow] = field(default_factory=list)
    already_imported: list[BiometricsRow] = field(default_factory=list)
    skipped_rows: int = 0

    @property
    def total_rows(self) -> int:
        return (
            len(self.matched) + len(self.unmatched) + len(self.conflicts)
            + len(self.present_rows) + len(self.already_imported) + self.skipped_rows
        )


def parse_inclusive_date_range(text: str) -> tuple[date | None, date | None]:
    """Best-effort parse of a free-text inclusive_dates string (as stored on
    leave_entries) into a (start_date, end_date) range.

    SAFETY NOTE: returns (None, None) if the text can't be confidently
    parsed. Callers MUST treat that as "unknown / could not verify" - NOT as
    "no conflict" - because this check exists specifically to avoid
    wrongfully deducting credit for a day the employee was already on
    approved leave. A parse failure should make a row MORE cautious
    (flagged for manual review), never less.

    Handles the patterns actually seen in this app's data:
      'JUNE 29-30, 2026'         -> June 29 to June 30, 2026 (same month/year)
      'JUNE 29, 2026'            -> June 29 to June 29, 2026 (single day)
      'June 29 - July 2, 2026'   -> June 29 to July 2, 2026 (range across months)
      '2026-06-29'               -> single ISO date
      '2026-06-29 to 2026-06-30' -> explicit ISO range
    """
    text = clean_text(text)
    if not text:
        return None, None

    same_month_range = re.match(r"^([A-Za-z]+)\s+(\d{1,2})\s*-\s*(\d{1,2})\s*,?\s*(\d{4})$", text)
    if same_month_range:
        month_name, day1, day2, year = same_month_range.groups()
        try:
            start = dateparser.parse(f"{month_name} {day1}, {year}")
            end = dateparser.parse(f"{month_name} {day2}, {year}")
            return start.date(), end.date()
        except (ValueError, OverflowError):
            pass

    range_parts = re.split(r"\s+to\s+|\s+-\s+(?=[A-Za-z0-9])", text, maxsplit=1)
    if len(range_parts) == 2:
        try:
            start = dateparser.parse(range_parts[0].strip())
            end = dateparser.parse(range_parts[1].strip())
            if start and end:
                return start.date(), end.date()
        except (ValueError, OverflowError):
            pass

    try:
        parsed = dateparser.parse(text, fuzzy=False)
        if parsed:
            return parsed.date(), parsed.date()
    except (ValueError, OverflowError):
        pass

    return None, None


def _date_falls_in_existing_leave(target_date_iso: str, employee_id: int, leave_all: pd.DataFrame) -> bool:
    """True if target_date_iso falls within any leave_entries row already on
    file for this employee (checking BOTH date_of_filing and any parseable
    inclusive_dates range), excluding biometrics-sourced rows themselves
    (so a deduction never "conflicts" with itself or a prior import)."""
    if leave_all.empty or not target_date_iso:
        return False
    try:
        target = dateparser.parse(target_date_iso).date()
    except (ValueError, OverflowError):
        return False

    employee_leaves = leave_all[
        (leave_all["employee_id"] == employee_id) & (leave_all.get("source_kind", "") != "biometrics")
    ]
    for _, row in employee_leaves.iterrows():
        filing_date_iso = clean_text(row.get("date_of_filing", ""))
        if filing_date_iso:
            try:
                if dateparser.parse(filing_date_iso).date() == target:
                    return True
            except (ValueError, OverflowError):
                pass

        start, end = parse_inclusive_date_range(row.get("inclusive_dates", ""))
        if start is not None and end is not None and start <= target <= end:
            return True

    return False


def _find_column(df: pd.DataFrame, candidates: list[str]) -> str | None:
    lower_map = {str(c).strip().lower(): c for c in df.columns}
    for candidate in candidates:
        key = candidate.strip().lower()
        if key in lower_map:
            return lower_map[key]
    return None


def _deduction_for_status(
    status: str,
    minutes_late: float | None,
    absent_deduction_days: float = DEFAULT_ABSENT_DEDUCTION_DAYS,
    late_deduction_days: float = DEFAULT_LATE_DEDUCTION_DAYS,
) -> tuple[str, float] | None:
    """Returns (leave_type, deduction_days) or None if the status isn't a
    deductible event (e.g. blank, 'PRESENT', 'ON LEAVE'). Deduction amounts
    are passed in (from form6_settings.get_biometrics_settings()) rather than
    hardcoded, so they reflect whatever the admin has configured in-app."""
    normalized = clean_text(status).upper()
    if "ABSENT" in normalized:
        return LEAVE_TYPE_ABSENT, absent_deduction_days
    if "LATE" in normalized:
        return LEAVE_TYPE_LATE, late_deduction_days
    return None


STATUS_PRESENT = "PRESENT"


def _is_present_status(status: str) -> bool:
    """True for statuses that represent a normal/on-time attendance day -
    kept separate from _deduction_for_status() since "is this a deduction"
    and "is this a present day" are different questions (e.g. a status this
    app doesn't recognize at all is neither, and stays in skipped_rows)."""
    return clean_text(status).upper() == STATUS_PRESENT


def read_biometrics_excel(file) -> tuple[pd.DataFrame, list[str]]:
    """Read the uploaded Excel file and return (raw_dataframe, warnings).
    Column names are matched case-insensitively against EXPECTED_COLUMNS;
    any that can't be found are reported as warnings rather than raising,
    so the preview screen can show the user exactly what went wrong."""
    warnings: list[str] = []
    df = pd.read_excel(file)

    resolved = {}
    for field_name, candidates in EXPECTED_COLUMNS.items():
        col = _find_column(df, candidates)
        if col is None and field_name != "minutes_late":
            warnings.append(f"Could not find a column for '{field_name}' (tried: {', '.join(candidates)}).")
        resolved[field_name] = col

    rename_map = {v: k for k, v in resolved.items() if v is not None}
    df = df.rename(columns=rename_map)
    return df, warnings


def _strip_trailing_initial(display_name: str) -> str:
    """Strip a single trailing middle-initial token, e.g. 'JUAN A.' -> 'JUAN',
    'DELA CRUZ, JUAN A' -> 'DELA CRUZ, JUAN'. Only strips a single letter
    (optionally followed by a period) at the very end of the name, so real
    short last words (e.g. 'JR.', 'III') are left alone on purpose - this is
    specifically for the common case of a lone middle initial."""
    text = clean_text(display_name)
    return re.sub(r"\s+[A-Za-z]\.?$", "", text).strip()


def _biometrics_leave_key(employee_id: int, date_iso: str, leave_type: str, credit_scope: str) -> str:
    return f"biometrics:leave:{employee_id}:{date_iso}:{leave_type}:{credit_scope.upper()}"


def _biometrics_already_imported(employee_id: int, date_iso: str, leave_type: str, leave_all: pd.DataFrame) -> bool:
    """True when this employee/date/type was already imported as a biometrics deduction."""
    if leave_all.empty or not date_iso:
        return False
    employee_leaves = leave_all[
        (leave_all["employee_id"] == employee_id) & (leave_all.get("source_kind", "") == "biometrics")
    ]
    if employee_leaves.empty:
        return False
    filing_dates = pd.to_datetime(employee_leaves["date_of_filing"], errors="coerce").dt.strftime("%Y-%m-%d")
    leave_types = employee_leaves["leave_type"].fillna("").astype(str).str.upper()
    return bool(((filing_dates == date_iso) & (leave_types == str(leave_type).upper())).any())


def build_import_preview(
    df: pd.DataFrame,
    employees: pd.DataFrame,
    leave_all: pd.DataFrame,
    absent_deduction_days: float = DEFAULT_ABSENT_DEDUCTION_DAYS,
    late_deduction_days: float = DEFAULT_LATE_DEDUCTION_DAYS,
) -> BiometricsImportPreview:
    """Match each biometrics row to an existing employee by normalized name,
    and compute the deduction using the given rates (pass in
    form6_settings.get_biometrics_settings() values - the defaults here are
    only a fallback). Does NOT write to the database - this is a dry-run
    preview so nothing is deducted before the admin confirms it.

    Matching tries two passes:
      1. Exact normalized name match (preferred - least likely to mix up
         two different people).
      2. Fallback match with a trailing middle-initial stripped from the
         EMPLOYEE record's name, since biometrics devices typically only
         capture a first/last name with no middle initial, while employee
         records here are usually stored as "LAST, FIRST M.".
    The biometrics row's own name is also tried both as-is and with any
    trailing initial stripped, to cover either side having the initial.

    SAFETY CHECK: a matched row whose date falls within an employee's
    EXISTING approved leave (date_of_filing or a parsed inclusive_dates
    range) is pulled into preview.conflicts instead of preview.matched.
    This catches the case where a biometrics device logs "ABSENT" simply
    because there was no punch that day - which is expected and correct
    when the employee was on approved leave, not an unexcused absence.
    Conflicts are never auto-imported; the admin must resolve them manually.
    """
    preview = BiometricsImportPreview()

    if "name" not in df.columns or "date" not in df.columns or "status" not in df.columns:
        return preview

    exact_lookup: dict[str, tuple[int, str]] = {}
    fallback_lookup: dict[str, tuple[int, str]] = {}
    fallback_key_counts: dict[str, int] = {}
    if not employees.empty:
        for _, row in employees.iterrows():
            employee_id = int(row["id"])
            employee_label = str(row.get("employee_label", ""))
            for candidate_name in (row.get("display_name", ""), row.get("employee_label", "")):
                exact_key = normalize_key(candidate_name)
                if exact_key:
                    exact_lookup[exact_key] = (employee_id, employee_label)
            # Only display_name (not employee_label, which also bakes in the
            # grade) is used for the fallback key, since stripping an initial
            # from the grade-prefixed label isn't meaningful.
            fallback_key = normalize_key(_strip_trailing_initial(str(row.get("display_name", ""))))
            if fallback_key:
                fallback_lookup[fallback_key] = (employee_id, employee_label)
                fallback_key_counts[fallback_key] = fallback_key_counts.get(fallback_key, 0) + 1

    ambiguous_fallback_keys = {key for key, count in fallback_key_counts.items() if count > 1}

    for _, row in df.iterrows():
        raw_name = clean_text(row.get("name", ""))
        status = clean_text(row.get("status", ""))
        date_iso = date_to_iso(row.get("date", ""))

        if not raw_name or not status:
            preview.skipped_rows += 1
            continue

        if not date_iso:
            preview.skipped_rows += 1
            continue

        is_present = _is_present_status(status)
        minutes_late = row.get("minutes_late") if "minutes_late" in df.columns else None
        deduction = None if is_present else _deduction_for_status(status, minutes_late, absent_deduction_days, late_deduction_days)

        if not is_present and deduction is None:
            # Status isn't recognized as PRESENT, ABSENT, or LATE - skip
            # rather than guess at what it means (e.g. an unexpected label
            # the biometrics device uses that this app doesn't know about).
            preview.skipped_rows += 1
            continue

        raw_key = normalize_key(raw_name)
        raw_key_stripped = normalize_key(_strip_trailing_initial(raw_name))

        match = exact_lookup.get(raw_key) or exact_lookup.get(raw_key_stripped)
        if not match:
            fallback_key = raw_key if raw_key in fallback_lookup else raw_key_stripped
            if fallback_key in fallback_lookup and fallback_key not in ambiguous_fallback_keys:
                match = fallback_lookup[fallback_key]

        if is_present:
            biometrics_row = BiometricsRow(
                raw_name=raw_name,
                matched_employee_id=match[0] if match else None,
                matched_label=match[1] if match else None,
                date_iso=date_iso,
                status=STATUS_PRESENT,
                deduction_days=0.0,
            )
            if match:
                preview.present_rows.append(biometrics_row)
            else:
                preview.unmatched.append(biometrics_row)
            continue

        leave_type, deduction_days = deduction

        biometrics_row = BiometricsRow(
            raw_name=raw_name,
            matched_employee_id=match[0] if match else None,
            matched_label=match[1] if match else None,
            date_iso=date_iso,
            status=leave_type,
            deduction_days=deduction_days,
        )

        if match and _date_falls_in_existing_leave(date_iso, match[0], leave_all):
            preview.conflicts.append(biometrics_row)
        elif match and _biometrics_already_imported(match[0], date_iso, leave_type, leave_all):
            preview.already_imported.append(biometrics_row)
        elif match:
            preview.matched.append(biometrics_row)
        else:
            preview.unmatched.append(biometrics_row)

    return preview


def commit_import(conn, preview: BiometricsImportPreview, credit_scope: str, source_ref: str) -> int:
    """Write the import results to the database:
      - preview.matched rows become leave_entries deductions against the
        chosen LOCAL/DIVISION scope (unchanged from before this feature).
      - preview.matched, preview.conflicts, and preview.present_rows are ALL
        also written to attendance_records as the raw daily attendance fact
        for that employee/date - this powers the Attendance Summary and is
        independent of whether a credit deduction happened. Conflicts are
        included here (with their original ABSENT/LATE status) because the
        biometrics device's reading is still a real attendance fact even
        though no deduction was made; the Attendance Summary derives "On
        Leave" separately by checking leave_entries, so this doesn't risk
        double-counting a leave day as both "On Leave" and "Absent".
      - preview.unmatched rows are NEVER written anywhere.
    Returns the number of leave_entries deduction rows inserted (matched
    count only, for the existing confirmation message)."""
    scope = clean_text(credit_scope).upper()
    inserted = 0
    for row in preview.matched:
        record_key = _biometrics_leave_key(row.matched_employee_id, row.date_iso, row.status, scope)
        before = conn.total_changes
        insert_leave_entry(
            conn,
            employee_id=row.matched_employee_id,
            date_of_filing=row.date_iso,
            inclusive_dates=row.date_iso,
            month="",
            no_days=row.deduction_days,
            no_halfdays=0,
            leave_type=row.status,
            service_credit_availed=scope,
            source_kind="biometrics",
            source_ref=source_ref,
            record_key=record_key,
        )
        if conn.total_changes > before:
            inserted += 1
        insert_attendance_record(
            conn, employee_id=row.matched_employee_id, record_date=row.date_iso,
            status=row.status, source_ref=source_ref,
        )

    for row in preview.conflicts:
        insert_attendance_record(
            conn, employee_id=row.matched_employee_id, record_date=row.date_iso,
            status=row.status, source_ref=source_ref,
        )

    for row in preview.present_rows:
        insert_attendance_record(
            conn, employee_id=row.matched_employee_id, record_date=row.date_iso,
            status=STATUS_PRESENT, source_ref=source_ref,
        )

    return inserted
