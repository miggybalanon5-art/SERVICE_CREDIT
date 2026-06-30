"""
Authentication, session management, and the login/registration page for the
Form 6 Tracker.

Split out of app.py so login/session logic can be read, tested, and modified
on its own, without the rest of the app's tab-rendering code in the way.
"""

from __future__ import annotations

import csv
import hashlib
from hmac import compare_digest
import os
import re
import secrets
import sys
import time
from datetime import datetime

import streamlit as st

from form6_cache import get_clean_state_frames
from form6_store import ensure_database

# ----------------------------------------------------------------------------
# PATHS & CONSTANTS
# ----------------------------------------------------------------------------
if getattr(sys, "frozen", False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

USERS_FILE = os.path.join(BASE_DIR, "system_users.csv")
BROWSER_SESSIONS_FILE = os.path.join(BASE_DIR, "browser_sessions.csv")
LOG_FILE = os.path.join(BASE_DIR, "audit_log.csv")
LOGO_PATH = os.path.join(BASE_DIR, "SCHOOL_LOGO.png")

SESSION_TIMEOUT_MINUTES = 30
MAX_LOGIN_ATTEMPTS = 5
LOCKOUT_SECONDS = 5 * 60
PASSWORD_HASH_ITERATIONS = 260_000
PASSWORD_HASH_SCHEME = "pbkdf2_sha256"

# Self-registered Employee Portal accounts pick their own employee_id from a
# dropdown during signup with no verification that the person registering
# actually IS that employee - anyone who has the shared EMPLOYEE_SECRET
# code can claim any unclaimed employee's record. ACCOUNT_STATUS exists to
# close that gap: new employee accounts are created PENDING and can log in,
# but employee_portal.py shows an "awaiting approval" screen instead of any
# real data until an admin explicitly approves the employee_id link (see
# the Account Approvals section in tab_employees.py). Staff/Admin accounts
# are approved immediately - they're already gated by ADMIN_SECRET and
# don't carry an employee_id link to misuse.
ACCOUNT_STATUS_PENDING = "pending"
ACCOUNT_STATUS_APPROVED = "approved"
ACCOUNT_STATUS_REJECTED = "rejected"


def _configured_secret(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "")
        if value:
            return value.strip()
        try:
            value = st.secrets.get(name, "")
        except Exception:
            value = ""
        if value:
            return str(value).strip()
    return ""


ADMIN_SECRET = _configured_secret("ADMIN_AUTH_CODE", "ADMIN_SECRET")
EMPLOYEE_SECRET = _configured_secret("EMPLOYEE_AUTH_CODE", "EMPLOYEE_SECRET")


# ----------------------------------------------------------------------------
# PASSWORD / USERNAME HELPERS
# ----------------------------------------------------------------------------
def normalize_username(username: str) -> str:
    return str(username or "").strip().lower()


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_HASH_ITERATIONS,
    ).hex()
    return f"{PASSWORD_HASH_SCHEME}${PASSWORD_HASH_ITERATIONS}${salt}${digest}"


def legacy_sha256_password(password: str) -> str:
    return hashlib.sha256(str(password).encode("utf-8")).hexdigest()


def is_sha256_hash(value: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-f]{64}", str(value or "").strip().lower()))


def is_pbkdf2_hash(value: str) -> bool:
    parts = str(value or "").split("$")
    if len(parts) != 4 or parts[0] != PASSWORD_HASH_SCHEME:
        return False
    try:
        iterations = int(parts[1])
    except ValueError:
        return False
    return iterations > 0 and bool(parts[2]) and bool(parts[3])


def is_password_hash(value: str) -> bool:
    return is_sha256_hash(value) or is_pbkdf2_hash(value)


def verify_password(password: str, stored_hash: str) -> bool:
    stored_hash = str(stored_hash or "").strip()
    if is_sha256_hash(stored_hash):
        return compare_digest(stored_hash, legacy_sha256_password(password))
    if not is_pbkdf2_hash(stored_hash):
        return False
    _, iterations_raw, salt, expected = stored_hash.split("$", 3)
    try:
        iterations = int(iterations_raw)
    except ValueError:
        return False
    actual = hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return compare_digest(actual, expected)


def check_password_strength(password: str) -> tuple[bool, str]:
    if len(password) < 8:
        return False, "Password must be at least 8 characters long."
    if not re.search(r"[A-Z]", password):
        return False, "Password must contain at least one uppercase letter."
    if not re.search(r"\d", password):
        return False, "Password must contain at least one number."
    return True, "Strong password."


# ----------------------------------------------------------------------------
# USERS CSV STORAGE
# ----------------------------------------------------------------------------
def get_users_fieldnames() -> list[str]:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, mode="r", encoding="utf-8") as f:
                reader = csv.reader(f)
                headers = next(reader, [])
                if headers:
                    return headers
        except Exception:
            pass
    return ["username", "role", "password_hash", "failed_attempts", "lockout_until", "employee_id", "account_status"]


def init_users():
    if not os.path.exists(USERS_FILE):
        with open(USERS_FILE, mode="w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(get_users_fieldnames())


def load_users() -> dict:
    init_users()
    users = {}
    with open(USERS_FILE, mode="r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            username = normalize_username(row.get("username", ""))
            if username:
                password_hash = str(row.get("password_hash", "")).strip()
                role = str(row.get("role", "user")).strip() or "user"
                if not is_password_hash(password_hash) and is_password_hash(role):
                    password_hash, role = role, password_hash or "user"
                employee_id_raw = str(row.get("employee_id", "") or "").strip()
                employee_id = int(employee_id_raw) if employee_id_raw.isdigit() else None
                # Backward compatibility: accounts created before this column
                # existed have a blank account_status. Treat blank as already
                # approved rather than locking out every pre-existing employee
                # account the moment this update ships - the approval gate is
                # meant to catch NEW unverified signups going forward, not
                # retroactively block people who were already using the app.
                account_status = str(row.get("account_status", "") or "").strip().lower()
                if account_status not in (ACCOUNT_STATUS_PENDING, ACCOUNT_STATUS_APPROVED, ACCOUNT_STATUS_REJECTED):
                    account_status = ACCOUNT_STATUS_APPROVED
                try:
                    failed_attempts = int(float(row.get("failed_attempts", "") or 0))
                except (TypeError, ValueError):
                    failed_attempts = 0
                try:
                    lockout_until = float(row.get("lockout_until", "") or 0)
                except (TypeError, ValueError):
                    lockout_until = 0.0
                users[username] = {
                    "hash": password_hash,
                    "role": role.lower(),
                    "employee_id": employee_id,
                    "account_status": account_status,
                    "failed_attempts": max(0, failed_attempts),
                    "lockout_until": max(0.0, lockout_until),
                }
    return users


def update_user_security_fields(
    username: str,
    *,
    password_hash: str | None = None,
    failed_attempts: int | None = None,
    lockout_until: float | None = None,
) -> bool:
    username = normalize_username(username)
    init_users()
    fieldnames = get_users_fieldnames()
    for field in ["password_hash", "failed_attempts", "lockout_until"]:
        if field not in fieldnames:
            fieldnames.append(field)

    with open(USERS_FILE, mode="r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    changed = False
    for row in rows:
        if normalize_username(row.get("username", "")) != username:
            continue
        if password_hash is not None:
            row["password_hash"] = password_hash
        if failed_attempts is not None:
            row["failed_attempts"] = str(max(0, int(failed_attempts)))
        if lockout_until is not None:
            row["lockout_until"] = str(max(0.0, float(lockout_until)))
        changed = True
        break

    if not changed:
        return False

    with open(USERS_FILE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    return True


def save_new_user(
    username: str,
    password: str,
    role: str = "user",
    employee_id: int | None = None,
    account_status: str | None = None,
) -> bool:
    username = normalize_username(username)
    users = load_users()
    if not username or username in users:
        return False
    if account_status is None:
        # Self-registered employee accounts start PENDING and need an admin
        # to approve the employee_id link before any real data is shown.
        # Staff/Admin accounts are approved immediately.
        account_status = ACCOUNT_STATUS_PENDING if role == "employee" else ACCOUNT_STATUS_APPROVED
    fieldnames = get_users_fieldnames()
    if "username" not in fieldnames:
        fieldnames.insert(0, "username")
    if "role" not in fieldnames:
        fieldnames.append("role")
    if "password_hash" not in fieldnames:
        fieldnames.append("password_hash")
    if "failed_attempts" not in fieldnames:
        fieldnames.append("failed_attempts")
    if "lockout_until" not in fieldnames:
        fieldnames.append("lockout_until")
    if "employee_id" not in fieldnames:
        fieldnames.append("employee_id")
    if "account_status" not in fieldnames:
        fieldnames.append("account_status")
    with open(USERS_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        row = {field: "" for field in fieldnames}
        row["username"] = username
        row["role"] = role
        row["password_hash"] = hash_password(password)
        row["failed_attempts"] = "0"
        row["lockout_until"] = "0"
        row["employee_id"] = "" if employee_id is None else str(int(employee_id))
        row["account_status"] = account_status
        writer.writerow(row)
    return True


def set_account_status(username: str, account_status: str) -> bool:
    """Admin action: approve or reject a pending Employee Portal account's
    employee_id link. Until approved, employee_portal.py refuses to show
    that account's real data regardless of what employee_id is stored."""
    if account_status not in (ACCOUNT_STATUS_PENDING, ACCOUNT_STATUS_APPROVED, ACCOUNT_STATUS_REJECTED):
        raise ValueError(f"Invalid account_status: {account_status!r}")
    username = normalize_username(username)
    init_users()
    fieldnames = get_users_fieldnames()
    if "account_status" not in fieldnames:
        fieldnames.append("account_status")
    with open(USERS_FILE, mode="r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    found = False
    for row in rows:
        if normalize_username(row.get("username", "")) == username:
            row["account_status"] = account_status
            found = True
            break
    if not found:
        return False
    with open(USERS_FILE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    return True


def list_pending_accounts() -> list[dict]:
    """Returns every account currently awaiting admin approval, as a list
    of {username, employee_id} dicts, for the admin Account Approvals UI."""
    users = load_users()
    return [
        {"username": username, "employee_id": info.get("employee_id")}
        for username, info in users.items()
        if info.get("account_status") == ACCOUNT_STATUS_PENDING
    ]


def set_employee_link(username: str, employee_id: int | None, role: str | None = None) -> bool:
    """Link/relink/unlink a user account to an employee record. Enforces a 1:1 link by
    releasing the employee from any other account that currently holds it.

    When an admin sets a real (non-None) link through this function, the
    account is also auto-approved - an admin manually choosing the link
    removes the unverified self-claim risk that account_status exists to
    guard against in the first place. Setting employee_id back to None
    (unlinking) does NOT change account_status, so re-linking later still
    goes through this same approved path rather than silently re-pending."""
    username = normalize_username(username)
    init_users()
    fieldnames = get_users_fieldnames()
    if "employee_id" not in fieldnames:
        fieldnames.append("employee_id")
    if "account_status" not in fieldnames:
        fieldnames.append("account_status")
    with open(USERS_FILE, mode="r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    found = False
    target_value = "" if employee_id is None else str(int(employee_id))
    for row in rows:
        row_user = normalize_username(row.get("username", ""))
        if target_value and str(row.get("employee_id", "")).strip() == target_value and row_user != username:
            row["employee_id"] = ""
        if row_user == username:
            found = True
            if role is not None:
                row["role"] = role
            row["employee_id"] = target_value
            if employee_id is not None:
                row["account_status"] = ACCOUNT_STATUS_APPROVED
    if not found:
        return False
    with open(USERS_FILE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fieldnames})
    return True


# ----------------------------------------------------------------------------
# AUDIT LOG
# ----------------------------------------------------------------------------
def log_action(username: str, action: str, details: str):
    file_exists = os.path.exists(LOG_FILE)
    with open(LOG_FILE, mode="a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["Timestamp", "User", "Action", "Details"])
        writer.writerow([datetime.now().strftime("%Y-%m-%d %H:%M:%S"), username, action, details])


# ----------------------------------------------------------------------------
# QUERY-PARAM SESSION TOKEN (for browser-refresh persistence)
# ----------------------------------------------------------------------------
def get_session_query_token() -> str:
    clear_session_query_token()
    return ""


def set_session_query_token(token: str):
    # Deliberately no-op: auth tokens must not be placed in URLs.
    return


def clear_session_query_token():
    try:
        if "session" in st.query_params:
            del st.query_params["session"]
    except Exception:
        try:
            params = st.experimental_get_query_params()
            if "session" in params:
                del params["session"]
            st.experimental_set_query_params(**params)
        except Exception:
            pass


# ----------------------------------------------------------------------------
# BROWSER SESSION PERSISTENCE (CSV-backed)
# ----------------------------------------------------------------------------
def _session_token_hash(token: str) -> str:
    return hashlib.sha256(str(token or "").encode("utf-8")).hexdigest()


def _session_row_matches(row: dict, token: str) -> bool:
    if not token:
        return False
    stored_hash = str(row.get("token_hash", "") or "").strip().lower()
    if stored_hash:
        return compare_digest(stored_hash, _session_token_hash(token))
    legacy_token = str(row.get("token", "") or "")
    return bool(legacy_token) and compare_digest(legacy_token, token)


def read_browser_sessions() -> list[dict]:
    if not os.path.exists(BROWSER_SESSIONS_FILE):
        return []
    try:
        with open(BROWSER_SESSIONS_FILE, mode="r", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except Exception:
        return []


def write_browser_sessions(rows: list[dict]):
    with open(BROWSER_SESSIONS_FILE, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["token_hash", "username", "role", "created_at", "last_seen", "expires_at"])
        for row in rows:
            writer.writerow([
                row.get("token_hash", ""), row.get("username", ""), row.get("role", "user"),
                row.get("created_at", ""), row.get("last_seen", ""), row.get("expires_at", "")
            ])


def create_browser_session(username: str, role: str) -> str:
    now = time.time()
    token = secrets.token_urlsafe(32)
    rows = []
    for row in read_browser_sessions():
        try:
            if float(row.get("expires_at") or 0) > now:
                rows.append(row)
        except Exception:
            pass
    rows.append({
        "token_hash": _session_token_hash(token), "username": username, "role": role,
        "created_at": str(now), "last_seen": str(now),
        "expires_at": str(now + (SESSION_TIMEOUT_MINUTES * 60)),
    })
    write_browser_sessions(rows)
    return token


def restore_browser_session(token: str) -> dict | None:
    if not token:
        return None
    now = time.time()
    users_db = load_users()
    rows = read_browser_sessions()
    kept_rows = []
    restored = None
    for row in rows:
        try:
            expires_at = float(row.get("expires_at") or 0)
        except Exception:
            expires_at = 0
        username = normalize_username(row.get("username", ""))
        if expires_at <= now or username not in users_db:
            continue
        if _session_row_matches(row, token):
            row["username"] = username
            row["role"] = users_db[username]["role"]
            row["token_hash"] = _session_token_hash(token)
            row["last_seen"] = str(now)
            row["expires_at"] = str(now + (SESSION_TIMEOUT_MINUTES * 60))
            restored = {
                "username": username, "role": row["role"], "token": token,
                "employee_id": users_db[username].get("employee_id"),
                "account_status": users_db[username].get("account_status", ACCOUNT_STATUS_APPROVED),
            }
        kept_rows.append(row)
    if len(kept_rows) != len(rows) or restored:
        write_browser_sessions(kept_rows)
    return restored


def remove_browser_session(token: str):
    if not token:
        return
    rows = [row for row in read_browser_sessions() if not _session_row_matches(row, token)]
    write_browser_sessions(rows)


def perform_logout() -> None:
    log_action(st.session_state.current_user, "LOGOUT", "Session disconnected.")
    remove_browser_session(st.session_state.get("session_token", ""))
    clear_session_query_token()
    st.session_state.logged_in = False
    st.session_state.current_user = None
    st.session_state.current_role = "user"
    st.session_state.current_employee_id = None
    st.session_state.current_account_status = None
    st.session_state.session_token = ""
    st.rerun()


# ----------------------------------------------------------------------------
# LOGIN / REGISTRATION PAGE
# ----------------------------------------------------------------------------
def login_page():
    # Insert Login-specific Custom CSS
    st.markdown(
        """
        <style>
        :root {
            --meta-blue: #0064E0;
            --meta-surface: var(--background-color);
            --meta-shadow: 0 1px 2px rgba(0,0,0,0.08), 0 2px 4px rgba(0,0,0,0.04);
            --meta-border-light: rgba(0,0,0,0.06);
            --r-lg: 12px;
        }
        @media (prefers-color-scheme: dark) {
            :root {
                --meta-border-light: rgba(255,255,255,0.07);
                --meta-shadow: 0 1px 2px rgba(0,0,0,0.4), 0 2px 4px rgba(0,0,0,0.3);
            }
        }
        @keyframes slideUpFade {
            from { opacity: 0; transform: translateY(12px); }
            to   { opacity: 1; transform: translateY(0); }
        }
        @keyframes fadeIn {
            from { opacity: 0; }
            to   { opacity: 1; }
        }
        .theme-text { color: var(--text-color) !important; }

        /* Subtle thin borders on form-style inputs so fields are easy to spot at a glance */
        div[data-baseweb="input"] > div,
        div[data-baseweb="textarea"] > div,
        div[data-baseweb="select"] > div {
            border: 1px solid color-mix(in srgb, var(--text-color) 16%, transparent) !important;
            border-radius: 6px !important;
            transition: border-color 0.15s ease-in-out;
        }
        div[data-baseweb="input"] > div:focus-within,
        div[data-baseweb="textarea"] > div:focus-within,
        div[data-baseweb="select"] > div:focus-within {
            border-color: var(--meta-blue) !important;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    st.markdown("<div style='height: 40px;'></div>", unsafe_allow_html=True)
    st.markdown(
        "<div style='animation: fadeIn 0.6s ease-out;'>"
        "<h1 style='text-align: center; font-weight: 700; letter-spacing: -0.025em;' class='theme-text'>"
        "CALAUAG NATIONAL HIGH SCHOOL</h1>",
        unsafe_allow_html=True
    )
    st.markdown(
        "<p style='text-align: center; color: var(--meta-blue); font-weight: 600; margin-bottom: 20px;'>"
        "Service Credit Tracker Portal</p></div>",
        unsafe_allow_html=True
    )

    col_spacer1, col_form, col_spacer3 = st.columns([1, 1.2, 1])

    with col_form:
        if time.time() < st.session_state.lockout_time:
            rem = int((st.session_state.lockout_time - time.time()) / 60)
            st.error(f"Too many failed attempts. Try again in {rem} minutes.")
            return

        st.markdown(
            "<div style='background: var(--meta-surface); padding: 30px; border-radius: var(--r-lg); "
            "box-shadow: var(--meta-shadow); border: 1px solid var(--meta-border-light); "
            "animation: slideUpFade 0.5s cubic-bezier(0.16, 1, 0.3, 1);'>",
            unsafe_allow_html=True
        )

        if os.path.exists(LOGO_PATH):
            c_img1, c_img2, c_img3 = st.columns([1, 1.5, 1])
            with c_img2: st.image(LOGO_PATH, use_container_width=True)

        tab_login, tab_register = st.tabs([" Secure Login", " Create Account"])

        with tab_login:
            log_username = st.text_input("Username", key="log_user", placeholder="Enter your username")
            log_password = st.text_input("Password", type="password", key="log_pass", placeholder="Enter your password")

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Log In", key="btn_login", use_container_width=True):
                log_username = normalize_username(log_username)
                users_db = load_users()
                user_record = users_db.get(log_username)
                now = time.time()
                if user_record and user_record.get("lockout_until", 0) > now:
                    remaining = max(1, int((user_record["lockout_until"] - now) / 60) + 1)
                    st.error(f"Too many failed attempts. Try again in {remaining} minute(s).")
                    return

                if user_record and verify_password(log_password, user_record["hash"]):
                    if is_sha256_hash(user_record["hash"]):
                        update_user_security_fields(log_username, password_hash=hash_password(log_password))
                    update_user_security_fields(log_username, failed_attempts=0, lockout_until=0)
                    st.session_state.failed_attempts = 0
                    st.session_state.logged_in = True
                    st.session_state.current_user = log_username
                    st.session_state.current_role = user_record["role"]
                    st.session_state.current_employee_id = user_record.get("employee_id")
                    st.session_state.current_account_status = user_record.get("account_status", ACCOUNT_STATUS_APPROVED)
                    session_token = create_browser_session(log_username, user_record["role"])
                    st.session_state.session_token = session_token
                    set_session_query_token(session_token)
                    log_action(log_username, "LOGIN", "Successful connection established.")
                    st.rerun()
                else:
                    if user_record:
                        attempts = int(user_record.get("failed_attempts", 0)) + 1
                        if attempts >= MAX_LOGIN_ATTEMPTS:
                            update_user_security_fields(
                                log_username,
                                failed_attempts=attempts,
                                lockout_until=now + LOCKOUT_SECONDS,
                            )
                            log_action(log_username, "LOCKOUT", "Exceeded max login attempts.")
                            st.error("Too many failed attempts. Locked out for 5 minutes.")
                            st.rerun()
                        else:
                            update_user_security_fields(log_username, failed_attempts=attempts)
                            st.error("Invalid username or password.")
                    else:
                        st.session_state.failed_attempts += 1
                        if st.session_state.failed_attempts >= MAX_LOGIN_ATTEMPTS:
                            st.session_state.lockout_time = now + LOCKOUT_SECONDS
                            log_action(log_username if log_username else "UNKNOWN", "LOCKOUT", "Exceeded max login attempts.")
                            st.error("Too many failed attempts. Locked out for 5 minutes.")
                            st.rerun()
                        else:
                            st.error("Invalid username or password.")

        with tab_register:
            reg_account_type = st.radio(
                "Account type", ["Staff / Admin Account", "Employee Portal Account"],
                horizontal=True, key="reg_account_type",
                help="Employee Portal accounts can only view that one employee's own leave and service credit records.",
            )
            reg_username = st.text_input("New Username", key="reg_user")
            reg_password = st.text_input("New Password", type="password", key="reg_pass")
            reg_confirm = st.text_input("Confirm Password", type="password", key="reg_conf")

            reg_employee_id = None
            reg_employee_available = True
            if reg_account_type == "Employee Portal Account":
                ensure_database()
                portal_employees, _, _, _ = get_clean_state_frames()
                users_for_links = load_users()
                linked_ids = {u.get("employee_id") for u in users_for_links.values() if u.get("employee_id")}
                available_employees = portal_employees[~portal_employees["id"].isin(linked_ids)] if not portal_employees.empty else portal_employees
                if available_employees.empty:
                    reg_employee_available = False
                    st.info("Every employee record already has a linked portal account. Ask your administrator to link your account from Manage Employees.")
                else:
                    link_map = dict(zip(available_employees["employee_label"], available_employees["id"]))
                    chosen_label = st.selectbox("This account belongs to", list(link_map.keys()), key="reg_employee_link")
                    reg_employee_id = link_map[chosen_label]
                st.caption(
                    "Employee Portal accounts are view-only and limited to the linked employee's own records. "
                    "An administrator will need to approve this link before you can see any records - you'll "
                    "be able to log in right away, but you'll see a pending-approval message until then."
                )

            is_employee_account = reg_account_type == "Employee Portal Account"
            reg_secret_label = "Employee Authorization Code" if is_employee_account else "Admin Authorization Code"
            reg_secret = st.text_input(reg_secret_label, type="password", key="reg_secret")

            st.markdown("<br>", unsafe_allow_html=True)
            if st.button("Create Account", key="btn_register", use_container_width=True):
                reg_username = normalize_username(reg_username)
                required_secret = EMPLOYEE_SECRET if is_employee_account else ADMIN_SECRET
                if not required_secret:
                    st.error("Account creation is disabled until authorization codes are configured in Streamlit secrets or environment variables.")
                elif not compare_digest(str(reg_secret or ""), required_secret):
                    st.error("Access Denied: Incorrect Authorization Code.")
                elif not reg_username or not reg_password:
                    st.warning("All fields are required.")
                elif reg_password != reg_confirm:
                    st.error("Passwords do not match.")
                elif is_employee_account and (not reg_employee_available or reg_employee_id is None):
                    st.error("Please select which employee record this account belongs to.")
                else:
                    is_strong, msg = check_password_strength(reg_password)
                    if not is_strong:
                        st.error(f"Weak Password: {msg}")
                    else:
                        final_role = "employee" if is_employee_account else "user"
                        if save_new_user(reg_username, reg_password, role=final_role, employee_id=reg_employee_id):
                            log_action(
                                "SYSTEM", "USER_CREATED",
                                f"New {final_role} account created for {reg_username}"
                                + (f" (requested link to employee_id={reg_employee_id}, pending admin approval)" if is_employee_account else "")
                            )
                            if is_employee_account:
                                st.success(
                                    f"Account for '{reg_username}' created! You can log in now, but an "
                                    f"administrator needs to approve your employee link before you'll see "
                                    f"any records."
                                )
                            else:
                                st.success(f"Account for '{reg_username}' created successfully! Please log in.")
                        else:
                            st.error("Username already exists.")

        st.markdown("</div>", unsafe_allow_html=True)
