"""
App-wide configurable settings, persisted in the app_settings table.

Currently used for the biometrics deduction rules (see form6_biometrics.py),
but written as a generic key-value store so future configurable values can
reuse it without another schema change.
"""

from __future__ import annotations

from form6_store import connect, ensure_database, now_iso

# Setting keys in use today. Centralized here so a typo can't silently create
# a second, disconnected setting under a slightly different key.
SETTING_ABSENT_DEDUCTION_DAYS = "biometrics.absent_deduction_days"
SETTING_LATE_DEDUCTION_DAYS = "biometrics.late_deduction_days"
SETTING_DEFAULT_CREDIT_SCOPE = "biometrics.default_credit_scope"
SETTING_RELIEVER_MINUTES_PER_POINT = "reliever.minutes_per_point"
SETTING_RELIEVER_POINTS_PER_CREDIT = "reliever.points_per_credit"

# Fallback defaults if a setting has never been saved (e.g. fresh database,
# or upgrading from a version of the app that predates this feature).
DEFAULT_ABSENT_DEDUCTION_DAYS = 1.0
DEFAULT_LATE_DEDUCTION_DAYS = 0.25
DEFAULT_CREDIT_SCOPE = "LOCAL"
# 1 reliever point = 45 minutes; 8 points = 360 minutes = 6 hours = 1 credit.
DEFAULT_RELIEVER_MINUTES_PER_POINT = 45.0
DEFAULT_RELIEVER_POINTS_PER_CREDIT = 8.0


def get_setting(key: str, default: str = "") -> str:
    ensure_database()
    with connect() as conn:
        row = conn.execute("SELECT setting_value FROM app_settings WHERE setting_key = ?", (key,)).fetchone()
    return row["setting_value"] if row is not None else default


def set_setting(key: str, value: str) -> None:
    ensure_database()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO app_settings (setting_key, setting_value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(setting_key) DO UPDATE SET
                setting_value = excluded.setting_value,
                updated_at = excluded.updated_at
            """,
            (key, value, now_iso()),
        )


def get_biometrics_settings() -> dict:
    """Returns the current deduction rules, falling back to the built-in
    defaults for any value that hasn't been explicitly configured yet."""
    try:
        absent_days = float(get_setting(SETTING_ABSENT_DEDUCTION_DAYS, str(DEFAULT_ABSENT_DEDUCTION_DAYS)))
    except ValueError:
        absent_days = DEFAULT_ABSENT_DEDUCTION_DAYS
    try:
        late_days = float(get_setting(SETTING_LATE_DEDUCTION_DAYS, str(DEFAULT_LATE_DEDUCTION_DAYS)))
    except ValueError:
        late_days = DEFAULT_LATE_DEDUCTION_DAYS
    default_scope = get_setting(SETTING_DEFAULT_CREDIT_SCOPE, DEFAULT_CREDIT_SCOPE).upper()
    if default_scope not in ("LOCAL", "DIVISION"):
        default_scope = DEFAULT_CREDIT_SCOPE
    return {
        "absent_deduction_days": absent_days,
        "late_deduction_days": late_days,
        "default_credit_scope": default_scope,
    }


def save_biometrics_settings(absent_deduction_days: float, late_deduction_days: float, default_credit_scope: str) -> None:
    set_setting(SETTING_ABSENT_DEDUCTION_DAYS, str(float(absent_deduction_days)))
    set_setting(SETTING_LATE_DEDUCTION_DAYS, str(float(late_deduction_days)))
    set_setting(SETTING_DEFAULT_CREDIT_SCOPE, str(default_credit_scope).upper())


def get_reliever_settings() -> dict:
    """Returns the current reliever point conversion rates, falling back to
    the built-in defaults (45 min/point, 8 points/credit = 6 hours) for any
    value that hasn't been explicitly configured yet."""
    try:
        minutes_per_point = float(
            get_setting(SETTING_RELIEVER_MINUTES_PER_POINT, str(DEFAULT_RELIEVER_MINUTES_PER_POINT))
        )
        if minutes_per_point <= 0:
            minutes_per_point = DEFAULT_RELIEVER_MINUTES_PER_POINT
    except ValueError:
        minutes_per_point = DEFAULT_RELIEVER_MINUTES_PER_POINT

    try:
        points_per_credit = float(
            get_setting(SETTING_RELIEVER_POINTS_PER_CREDIT, str(DEFAULT_RELIEVER_POINTS_PER_CREDIT))
        )
        if points_per_credit <= 0:
            points_per_credit = DEFAULT_RELIEVER_POINTS_PER_CREDIT
    except ValueError:
        points_per_credit = DEFAULT_RELIEVER_POINTS_PER_CREDIT

    return {
        "minutes_per_point": minutes_per_point,
        "points_per_credit": points_per_credit,
    }


def save_reliever_settings(minutes_per_point: float, points_per_credit: float) -> None:
    set_setting(SETTING_RELIEVER_MINUTES_PER_POINT, str(float(minutes_per_point)))
    set_setting(SETTING_RELIEVER_POINTS_PER_CREDIT, str(float(points_per_credit)))
