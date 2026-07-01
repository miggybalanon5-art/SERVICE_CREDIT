"""
Database write actions (insert/update/delete) and the undo stack for the
Form 6 Tracker.

Centralizing writes here means every save/delete path goes through the same
cache-invalidation step (clear_streamlit_cache), so we don't end up with a
stale-cache bug from a write path that forgets to invalidate.
"""

from __future__ import annotations

import pandas as pd
import streamlit as st

from form6_cache import invalidate_state_cache
from form6_store import build_employee_summary

UNDO_STACK_KEY = "undo_stack"
MAX_UNDO_HISTORY = 5
EMPLOYEE_DELETE_CONFIRM_KEY = "confirm_delete_employee_id"

PRESERVED_SESSION_KEYS = {
    "flash_message",
    UNDO_STACK_KEY,
    EMPLOYEE_DELETE_CONFIRM_KEY,
    "EDIT_EMPLOYEE_ID",
    "main_nav_tab",
    "emp_nav_tab",
    "encode_nav_tab",
    "ledger_nav_tab",
    "filter_search_term",
    "manage_search_term",
    "_sync_manage_to_filter",
    "filter_grades",
    "filter_employees",
    "filter_origins",
    "filter_leave_types",
    "filter_credit_scopes",
    "filter_date_range",
    "attendance_summary_month",
    "logged_in",
    "current_user",
    "current_role",
    "current_employee_id",
    "session_token",
    "last_activity",
    "failed_attempts",
    "lockout_time",
}
PRESERVED_SESSION_PREFIXES = ("filter_", "_")


def _should_preserve_session_key(key: str) -> bool:
    if key in PRESERVED_SESSION_KEYS:
        return True
    if key.startswith(PRESERVED_SESSION_PREFIXES):
        return True
    return False


# ----------------------------------------------------------------------------
# CACHE INVALIDATION + SESSION CLEANUP
# ----------------------------------------------------------------------------
def clear_streamlit_cache() -> None:
    """Call after any write. Clears the cached state (form6_cache) and the
    legacy global Streamlit caches, then drops transient session_state keys
    so stale UI state (e.g. an open edit form) doesn't linger after a rerun."""
    invalidate_state_cache()
    st.cache_data.clear()
    st.cache_resource.clear()
    for key in list(st.session_state.keys()):
        if not _should_preserve_session_key(key):
            del st.session_state[key]


# ----------------------------------------------------------------------------
# CONNECTION HELPER
# ----------------------------------------------------------------------------
def ensure_connection():
    """Returns a connection to the Neon PostgreSQL database."""
    return st.connection("postgresql", type="sql")

def clean_required_text(value: str) -> str:
    return str(value).strip()


def _last_insert_id(conn) -> int | None:
    try:
        row = conn.execute("SELECT last_insert_rowid()").fetchone()
        value = row[0] if row else None
        return int(value) if value else None
    except Exception:
        return None


# ----------------------------------------------------------------------------
# DELETE / UPDATE OPERATIONS
# ----------------------------------------------------------------------------
def delete_leave_entry_by_id(entry_id: int) -> bool:
    with ensure_connection() as conn:
        cur = conn.execute("DELETE FROM leave_entries WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


def delete_credit_entry_by_id(entry_id: int) -> bool:
    with ensure_connection() as conn:
        cur = conn.execute("DELETE FROM service_credit_entries WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


def delete_reliever_entry_by_id(entry_id: int) -> bool:
    """Delete one logged reliever points session.

    NOTE: this does NOT retract any LOCAL service credit that was already
    auto-issued from reliever points before this deletion. The conversion
    in form6_reliever.py is driven by the *current* cumulative point total,
    so deleting an old entry only affects future conversions going forward
    — it won't claw back a credit that's already on the ledger. If a
    credit needs to be removed too, delete it separately from the Service
    Credits ledger (look for source_kind='reliever' entries around the
    same date)."""
    with ensure_connection() as conn:
        cur = conn.execute("DELETE FROM reliever_points_entries WHERE id = ?", (entry_id,))
        return cur.rowcount > 0


def delete_employee_cascade(employee_id: int) -> dict[str, int]:
    with ensure_connection() as conn:
        leave_cur = conn.execute("DELETE FROM leave_entries WHERE employee_id = ?", (employee_id,))
        credit_cur = conn.execute("DELETE FROM service_credit_entries WHERE employee_id = ?", (employee_id,))
        emp_cur = conn.execute("DELETE FROM employees WHERE id = ?", (employee_id,))
        return {"employees": emp_cur.rowcount, "leave_entries": leave_cur.rowcount, "service_credits": credit_cur.rowcount}


def update_employee_record(employee_id: int, new_grade: str, new_position: str, new_name: str) -> bool:
    from form6_store import build_employee_key, clean_text, now_iso

    new_grade = clean_text(new_grade)
    new_position = clean_text(new_position)
    new_name = clean_text(new_name)
    new_key = build_employee_key(new_grade, new_name)

    with ensure_connection() as conn:
        existing = conn.execute("SELECT id FROM employees WHERE id = ?", (employee_id,)).fetchone()
        if existing is None:
            return False

        conflict = conn.execute(
            "SELECT id FROM employees WHERE employee_key = ? AND id != ?",
            (new_key, employee_id),
        ).fetchone()
        if conflict is not None:
            raise ValueError("Another employee already has this grade and name combination.")

        cur = conn.execute(
            """
            UPDATE employees
            SET employee_key = ?, grade = ?, position = ?, display_name = ?, updated_at = ?
            WHERE id = ?
            """,
            (new_key, new_grade, new_position, new_name, now_iso(), employee_id),
        )
        return cur.rowcount > 0


# ----------------------------------------------------------------------------
# UNDO STACK
# ----------------------------------------------------------------------------
def push_undo_action(kind: str, record_id: int, label: str) -> None:
    stack = st.session_state.get(UNDO_STACK_KEY, [])
    stack.append({"kind": kind, "id": record_id, "label": label})
    st.session_state[UNDO_STACK_KEY] = stack[-MAX_UNDO_HISTORY:]


def pop_undo_action() -> dict | None:
    stack = st.session_state.get(UNDO_STACK_KEY, [])
    if not stack: return None
    action = stack.pop()
    st.session_state[UNDO_STACK_KEY] = stack
    return action


def peek_undo_action() -> dict | None:
    stack = st.session_state.get(UNDO_STACK_KEY, [])
    return stack[-1] if stack else None


def render_undo_banner(key_suffix: str = "") -> None:
    action = peek_undo_action()
    if not action: return
    cols = st.columns([0.75, 0.25])
    with cols[0]: st.caption(f"Last action: {action['label']}")
    with cols[1]:
        if st.button("Undo", key=f"undo_btn_{action['kind']}_{action['id']}_{key_suffix}", use_container_width=True):
            success = False
            if action["kind"] == "leave": success = delete_leave_entry_by_id(action["id"])
            elif action["kind"] == "credit": success = delete_credit_entry_by_id(action["id"])
            elif action["kind"] == "employee":
                result = delete_employee_cascade(action["id"])
                success = result["employees"] > 0
            pop_undo_action()
            clear_streamlit_cache()
            if success: st.session_state["flash_message"] = {"message": f"Undone: {action['label']}", "kind": "warning"}
            else: st.session_state["flash_message"] = {"message": "Nothing to undo - that record was already removed.", "kind": "warning"}
            st.rerun()


# ----------------------------------------------------------------------------
# BALANCE LOOKUP (used after saves to show toast messages)
# ----------------------------------------------------------------------------
def get_summary_safe(employees, leaves, credits):
    """Build employee summary with accurate balances (may be negative)."""
    return build_employee_summary(employees, leaves, credits)


def available_credit(balance: float) -> float:
    """Credit available to spend — never below zero."""
    return max(0.0, float(balance or 0))


def credit_balance_for(employee_id: int, credit_scope: str, employees, leaves, credits) -> float:
    """Look up an employee's current LOCAL or DIVISION service credit balance for toast messages."""
    summary = get_summary_safe(employees, leaves, credits)
    if summary.empty:
        return 0.0
    row = summary[summary["id"] == employee_id] if "id" in summary.columns else summary.iloc[0:0]
    if row.empty:
        return 0.0
    column = "local_balance" if str(credit_scope).upper() == "LOCAL" else "national_balance"
    return float(row.iloc[0].get(column, 0) or 0)


def build_credit_ledger(leaves, credits, scope: str):
    """Build a running-balance ledger for one credit scope ('LOCAL' or 'DIVISION'),
    interleaving credit-earning events (service_credit_entries) and credit-spending
    events (leave_entries where service_credit_availed matches this scope), ordered
    oldest-to-newest by date. Mirrors the same earned-minus-used math as
    build_employee_summary() so the final running balance always agrees with the
    official balance shown elsewhere in the app.

    Returns a DataFrame with columns: date, description, change, running_balance.
    """
    scope = str(scope).upper()
    rows = []

    if not credits.empty:
        earn = credits[credits["credit_scope"].fillna("").astype(str).str.upper() == scope].copy()
        for _, r in earn.iterrows():
            rows.append({
                "date": r.get("event_date"),
                "description": f"Earned - {r.get('service_attended') or 'Service credit'}",
                "change": float(r.get("credit_units", 0) or 0),
            })

    if not leaves.empty:
        spend = leaves[leaves["service_credit_availed"].fillna("").astype(str).str.upper() == scope].copy()
        for _, r in spend.iterrows():
            rows.append({
                "date": r.get("date_of_filing"),
                "description": f"Used - {r.get('leave_type') or 'Leave'}",
                "change": -float(r.get("total_days", 0) or 0),
            })

    if not rows:
        return pd.DataFrame(columns=["date", "description", "change", "running_balance"])

    ledger = pd.DataFrame(rows)
    ledger["_sort_date"] = pd.to_datetime(ledger["date"], errors="coerce")
    ledger = ledger.sort_values(["_sort_date"], na_position="first").drop(columns=["_sort_date"]).reset_index(drop=True)
    ledger["running_balance"] = ledger["change"].cumsum()
    return ledger
