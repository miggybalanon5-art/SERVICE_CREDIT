"""
Shared UI building blocks for the Form 6 Tracker: CSS injection, flash/toast
messaging, and small formatting/export helpers used by multiple tabs.

Nothing in this file touches the database directly - it's presentation-only,
so it can be imported by any tab module without pulling in write logic.
"""

from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

HEADER_FILL = PatternFill("solid", fgColor="0064E0")
HEADER_FONT = Font(color="FFFFFF", bold=True)
LIGHT_BORDER = Border(
    left=Side(style="thin", color="CED0D4"),
    right=Side(style="thin", color="CED0D4"),
    top=Side(style="thin", color="CED0D4"),
    bottom=Side(style="thin", color="CED0D4"),
)

LEAVE_INTERNAL_COLS = ["id", "employee_id", "source_kind", "source_ref", "no_days", "no_halfdays"]
LEAVE_DISPLAY_RENAME = {
    "date_of_filing": "Date Filed", "month": "Month", "leave_type": "Leave Type",
    "total_days": "Days", "inclusive_dates": "Inclusive Dates",
    "service_credit_availed": "Credit Availed", "employee_label": "Employee", "grade": "Grade",
}
CREDIT_INTERNAL_COLS = ["id", "employee_id", "source_kind", "source_ref"]
CREDIT_DISPLAY_RENAME = {
    "credit_scope": "Scope", "event_date": "Date", "service_attended": "Service Attended",
    "credit_units": "Units", "employee_label": "Employee", "grade": "Grade",
}


def inject_app_css():
    st.markdown(
        """
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');

        /* --- Global Reset & Fluid Typography --- */
        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif !important;
            -webkit-font-smoothing: antialiased;
        }
        
        /* Mobile-first structural containment */
        .block-container { 
            padding: 1rem 0.75rem !important;
            max-width: 100% !important;
        }
        
        h1, h2, h3 { 
            font-weight: 700; 
            letter-spacing: -0.025em;
            line-height: 1.25;
        }
        
        /* --- High-Affordance Touch Targets (Minimum 48px height) --- */
        .stButton > button, 
        .stDownloadButton > button {
            width: 100% !important; /* Stack actions naturally by default on mobile screens */
            background-color: var(--primary-color, #0064E0);
            color: #FFFFFF !important;
            border: none;
            border-radius: 8px;
            padding: 0.75rem 1rem !important;
            font-weight: 600;
            font-size: 0.95rem;
            min-height: 48px !important;
            box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
            transition: background 0.15s ease-in-out, transform 0.1s ease;
        }
        .stButton > button:hover, 
        .stDownloadButton > button:hover { 
            opacity: 0.95; 
            background-color: #0052b8;
        }
        .stButton > button:active {
            transform: scale(0.98);
        }
        
        /* Secondary Action Alternates */
        .stDownloadButton > button {
            background-color: var(--secondary-background-color, #F0F2F6);
            color: var(--text-color, #31333F) !important;
            border: 1px solid color-mix(in srgb, var(--text-color) 12%, transparent);
        }
        .stDownloadButton > button:hover {
            background-color: color-mix(in srgb, var(--text-color) 8%, var(--secondary-background-color, #F0F2F6));
        }
        
        /* --- Responsive Fluid Metric System --- */
        div[data-testid="stMetric"] {
            background-color: var(--background-color);
            border: 1px solid color-mix(in srgb, var(--text-color) 10%, transparent);
            border-radius: 12px;
            padding: 1rem !important;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.02);
            width: 100%;
        }
        div[data-testid="stMetricValue"] {
            color: var(--primary-color, #0064E0);
            font-size: 1.75rem !important;
            font-weight: 700;
            line-height: 1.1;
        }
        div[data-testid="stMetricLabel"] { 
            font-size: 0.85rem !important; 
            font-weight: 600; 
            text-transform: uppercase; 
            letter-spacing: 0.05em;
            opacity: 0.8;
        }
        
        /* Metric container wrap-engine */
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) {
            display: flex !important;
            flex-flow: row wrap !important;
            gap: 8px !important;
            padding: 0 !important;
        }
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) > div[data-testid="column"] {
            flex: 1 1 calc(50% - 4px) !important;
            min-width: 140px !important;
        }

        /* --- Native Embedded Frame Constraints --- */
        iframe[title*="calendar" i],
        .fb-cover-calendar iframe,
        div[data-testid="stHtml"]:has(.fb-cover-calendar) {
            width: 100% !important;
            aspect-ratio: 16 / 10 !important; /* Upgraded from ultra-wide desktop aspect ratio for high mobile clarity */
            height: auto !important;
            border-radius: 10px;
            border: 1px solid var(--secondary-background-color);
        }
        
        /* --- Mobile-First Layout Tabs & Inputs --- */
        .stTabs [data-baseweb="tab-list"] {
            gap: 8px;
            border-bottom: 2px solid var(--secondary-background-color);
            overflow-x: auto;
            scrollbar-width: none; /* Hide standard desktop scrollbars */
            -webkit-overflow-scrolling: touch;
        }
        .stTabs [data-baseweb="tab-list"]::-webkit-scrollbar {
            display: none;
        }
        .stTabs [data-baseweb="tab"] {
            height: 46px;
            font-size: 0.9rem;
            font-weight: 600;
            padding: 0 12px !important;
        }
        
        /* Custom Pill Radios */
        div[role="radiogroup"] { 
            display: flex !important;
            flex-wrap: wrap; 
            gap: 8px; 
        }
        div[role="radiogroup"] label { 
            background-color: var(--background-color); 
            border: 1px solid color-mix(in srgb, var(--text-color) 15%, transparent); 
            border-radius: 24px; 
            padding: 6px 14px !important; 
            font-size: 0.85rem;
        }
        
        /* Form Card UI Cleanups */
        [data-testid="stForm"] { 
            padding: 1rem !important; 
            border-radius: 12px;
            border: 1px solid color-mix(in srgb, var(--text-color) 8%, transparent);
        }
        
        /* Form Inputs touch Optimization */
        div[data-baseweb="input"] > div,
        div[data-baseweb="select"] > div,
        .stTextInput input, .stSelectbox div {
            min-height: 44px !important;
            border-radius: 8px !important;
        }

        /* --- Desktop Media Queries (Scale Up smoothly for larger views) --- */
        @media (min-width: 769px) {
            .block-container {
                padding: 2rem 1.5rem !important;
                max-width: 1100px !important;
            }
            div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) > div[data-testid="column"] {
                flex: 1 1 0px !important;
            }
            .stButton > button, .stDownloadButton > button {
                width: auto !important;
            }
            iframe[title*="calendar" i] {
                aspect-ratio: 820 / 312 !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def confirm_destructive_action(action_id: str, action_label: str, action_type: str = "delete") -> bool:
    confirm_key = f"_confirm_{action_id}"
    
    if st.session_state.get(confirm_key):
        # Displays warning cleanly without breaking layouts on narrow viewports
        st.error(f"⚠️ **Confirm {action_type.title()}**\n\nThis action is permanent and cannot be undone!")
        
        # Grid blocks map smoothly across mobile screen widths cleanly
        col_action, col_cancel = st.columns(2)
        with col_action:
            executed = st.button(
                f"Yes, {action_type.title()}", 
                key=f"confirm_{action_id}", 
                use_container_width=True, 
                type="primary"
            )
        with col_cancel:
            if st.button("Cancel", key=f"cancel_{action_id}", use_container_width=True):
                st.session_state[confirm_key] = False
                st.rerun()
                
        if executed:
            st.session_state[confirm_key] = False
            return True
        return False
    else:
        if st.button(f"🗑️ {action_type.title()} {action_label}", key=f"trigger_{action_id}", use_container_width=True):
            st.session_state[confirm_key] = True
            st.rerun()
        return False


def keyed_tabs(labels: list[str], session_key: str):
    try:
        return st.tabs(labels, key=session_key)
    except TypeError:
        return st.tabs(labels)


def flash(message: str, kind: str = "success") -> None:
    st.session_state["flash_message"] = {"message": message, "kind": kind}


def show_flash() -> None:
    payload = st.session_state.pop("flash_message", None)
    if not payload: return
    kind = payload.get("kind", "success")
    message = payload.get("message", "")
    if kind == "error": st.error(message)
    elif kind == "warning": st.warning(message)
    else: st.success(message)


def queue_toast(message: str, icon: str = "✅") -> None:
    st.session_state["pending_toast"] = {"message": message, "icon": icon}


def show_pending_toast() -> None:
    payload = st.session_state.pop("pending_toast", None)
    if not payload: return
    st.toast(payload.get("message", ""), icon=payload.get("icon", "✅"))


def safe_text(value) -> str:
    if value is None: return ""
    if isinstance(value, float) and pd.isna(value): return ""
    if isinstance(value, (pd.Timestamp, datetime, date)):
        if pd.isna(value): return ""
        return pd.Timestamp(value).strftime("%Y-%m-%d")
    return str(value)


def readable_view(df: pd.DataFrame, drop: list[str] | None = None, rename: dict[str, str] | None = None) -> pd.DataFrame:
    if df.empty: return df.copy()
    drop = drop or []
    result = df.drop(columns=drop, errors="ignore").copy()
    for column in result.columns:
        if pd.api.types.is_datetime64_any_dtype(result[column]):
            result[column] = result[column].dt.strftime("%Y-%m-%d").fillna("")
    for column in result.columns:
        if pd.api.types.is_datetime64tz_dtype(result[column]):
            result[column] = result[column].dt.tz_convert(None).dt.strftime("%Y-%m-%d")
    if rename:
        result = result.rename(columns={k: v for k, v in rename.items() if k in result.columns})
    return result


def month_index_from_date(value: date | datetime | None) -> int:
    if value is None: return 0
    return max(0, min(11, pd.Timestamp(value).month - 1))


def date_bounds(leaves: pd.DataFrame, credits: pd.DataFrame) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    if leaves.empty or "date_of_filing" not in leaves.columns: return None, None
    series = pd.to_datetime(leaves["date_of_filing"], errors="coerce").dropna()
    if series.empty: return None, None
    return series.min(), series.max()


def employee_options(df: pd.DataFrame) -> dict[str, int]:
    if df.empty: return {}
    return dict(zip(df["employee_label"].tolist(), df["id"].tolist(), strict=False))


def render_metrics(employees: pd.DataFrame, leaves: pd.DataFrame) -> None:
    registered_employees = len(employees)
    today_ts = pd.Timestamp(date.today())
    upcoming_leaves_count = 0
    leaves_only = leaves[leaves.get("source_kind", "") != "biometrics"] if not leaves.empty else leaves
    if not leaves_only.empty and "date_of_filing" in leaves_only.columns:
        parsed_dates = pd.to_datetime(leaves_only["date_of_filing"], errors="coerce")
        upcoming_leaves_count = int((parsed_dates >= today_ts).sum())

    cols = st.columns(2)
    cols[0].metric("Total Employees", f"{registered_employees:,}")
    cols[1].metric("Ongoing / Upcoming", f"{upcoming_leaves_count:,}")


def export_workbook_bytes(tables: dict[str, pd.DataFrame]) -> bytes:
    workbook = Workbook()
    workbook.remove(workbook.active)
    for title, df in tables.items():
        ws = workbook.create_sheet(title=title[:31])
        view = readable_view(df)
        if view.empty:
            ws.append(["No rows available."])
            continue
        ws.append([str(col) for col in view.columns])
        for row in view.itertuples(index=False, name=None): ws.append(list(row))
        ws.freeze_panes = "A2"
        ws.auto_filter.ref = ws.dimensions
        for cell in ws[1]:
            cell.fill = HEADER_FILL
            cell.font = HEADER_FONT
            cell.alignment = Alignment(horizontal="center", vertical="center")
            cell.border = LIGHT_BORDER
        for row in ws.iter_rows(min_row=2):
            for cell in row:
                cell.border = LIGHT_BORDER
                cell.alignment = Alignment(vertical="top")
        for col_idx, column_name in enumerate(view.columns, start=1):
            values = [str(v)[:80] for v in view.iloc[:, col_idx - 1].fillna("").tolist()]
            max_len = max([len(str(column_name))] + [len(v) for v in values]) if values else len(str(column_name))
            ws.column_dimensions[get_column_letter(col_idx)].width = min(max(12, max_len + 2), 34)
    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def db_backup_bytes() -> bytes:
    from form6_store import DB_PATH, ensure_database
    ensure_database()
    return DB_PATH.read_bytes()
