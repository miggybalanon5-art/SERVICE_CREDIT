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

        /* =========================================================
           GLOBAL DESKTOP & GLASSMORPHISM BASE
           ========================================================= */
        html, body, [class*="css"] {
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
        }
        
        .block-container { 
            padding-top: 2rem; 
            padding-bottom: 2rem; 
            max-width: 1200px;
        }
        
        h1, h2, h3, h4, h5, h6 { font-weight: 600; }
        
        /* Glassmorphism for Metrics & Forms */
        div[data-testid="stMetric"], 
        [data-testid="stForm"] {
            background: rgba(30, 30, 35, 0.6) !important;
            backdrop-filter: blur(12px) !important;
            -webkit-backdrop-filter: blur(12px) !important;
            border: 1px solid rgba(255, 255, 255, 0.1) !important;
            border-radius: 12px !important;
            box-shadow: 0 4px 6px rgba(0, 0, 0, 0.1) !important;
        }

        /* Buttons Base */
        .stButton > button {
            background-color: var(--primary-color, #0064E0);
            color: #FFFFFF;
            border: none;
            border-radius: 6px;
            padding: 0.5rem 1rem;
            font-weight: 600;
            box-shadow: none;
            transition: opacity 0.2s;
            min-height: 48px !important; /* Optimized for touch */
        }
        .stButton > button:hover { opacity: 0.9; color: #FFFFFF; }
        .stButton > button:active { opacity: 0.8; color: #FFFFFF; }
        
        /* 4-Column Metric Grid (Desktop Default) */
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) {
            display: grid !important;
            grid-template-columns: repeat(4, 1fr) !important;
            gap: 16px !important;
            width: 100% !important;
        }
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) > div[data-testid="column"] {
            width: 100% !important;
            min-width: 0 !important;
        }
        
        div[data-testid="stMetric"] {
            padding: 1.2rem 0.5rem !important;
            display: flex !important;
            flex-direction: column !important;
            align-items: center !important;
            justify-content: center !important;
        }
        div[data-testid="stMetricValue"] {
            color: var(--primary-color, #0064E0);
            font-size: clamp(1.2rem, 3.5vw, 2.2rem) !important;
            font-weight: 700;
            line-height: 1.2 !important;
            text-align: center !important;
        }
        div[data-testid="stMetricLabel"] { 
            font-size: clamp(0.7rem, 1.8vw, 0.9rem) !important; 
            font-weight: 600; 
            text-align: center !important; 
            white-space: pre-wrap !important;
            margin-bottom: 4px !important;
        }

        /* Calendar & General Elements */
        div[data-testid="stHtml"], iframe[title*="calendar" i], .fb-cover-calendar iframe {
            width: 100% !important; max-width: 100% !important; border-radius: 8px;
        }
        [data-testid="stSidebar"] { border-right: 1px solid var(--secondary-background-color); }
        
        /* Desktop Tabs */
        .stTabs [data-baseweb="tab-list"] {
            gap: 20px; border-bottom: 1px solid rgba(255, 255, 255, 0.1); overflow-x: auto; flex-wrap: nowrap !important; scrollbar-width: thin;
        }
        .stTabs [data-baseweb="tab"] {
            height: 50px; white-space: nowrap; background-color: transparent; border-radius: 0; font-weight: 600;
        }
        .stTabs [aria-selected="true"] { color: var(--primary-color, #0064E0) !important; border-bottom: 3px solid var(--primary-color, #0064E0) !important; }
        
        [data-testid="stDataFrame"] { background-color: var(--background-color); border-radius: 8px; padding: 10px; overflow-x: auto; }

        /* =========================================================
           MOBILE RESPONSIVENESS (< 768px)
           Targets: 360x640, 393x851, 412x915
           ========================================================= */
        @media screen and (max-width: 768px) {
            
            /* 1. Spacing, Margins, & Chat Buffer */
            .block-container {
                padding-top: 1rem !important;
                padding-left: 1rem !important;
                padding-right: 1rem !important;
                padding-bottom: 6rem !important; /* Prevents floating chat overlap */
                max-width: 100vw !important;
            }
            [data-testid="stVerticalBlock"] > div { padding-bottom: 0.5rem !important; }

            /* 2. Typography Adjustments & Name Wrapping */
            h1, [data-testid="stHeadingContainer"] h1 {
                font-size: 1.6rem !important; /* Reduced ~40% */
                line-height: 1.2 !important;
                word-wrap: break-word !important;
                white-space: normal !important; /* Prevents employee name cutoff */
            }
            h2 { font-size: 1.4rem !important; }
            h3 { font-size: 1.1rem !important; }

            /* 3. Metric Cards: Convert to 2x2 Grid */
            div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) {
                grid-template-columns: repeat(2, 1fr) !important;
                gap: 8px !important;
            }

            /* Stack all other normal columns vertically */
            div[data-testid="stHorizontalBlock"]:not(:has(div[data-testid="stMetric"])) {
                flex-direction: column !important;
                width: 100% !important;
            }
            div[data-testid="stHorizontalBlock"]:not(:has(div[data-testid="stMetric"])) > div[data-testid="column"] {
                width: 100% !important;
            }

            /* 4. Tables: Horizontal Scrolling */
            [data-testid="stDataFrame"], [data-testid="stTable"] {
                width: 100% !important;
                max-width: 100vw !important;
                overflow-x: auto !important;
                display: block !important;
                padding: 0 !important;
            }

            /* 5. Touch Targets: Tabs & Buttons */
            .stTabs [data-baseweb="tab-list"] {
                gap: 5px !important;
                -webkit-overflow-scrolling: touch;
            }
            .stTabs [data-baseweb="tab"] {
                min-height: 48px !important;
                font-size: 0.9rem !important;
                padding: 0 12px !important;
            }

            /* 6. Form Elements: Dropdowns & Calendar Fit */
            [data-baseweb="select"], [data-testid="stDateInput"], iframe[title*="calendar" i], .fb-cover-calendar iframe, div[data-testid="stHtml"] {
                width: 100% !important;
                max-width: 100vw !important;
                aspect-ratio: auto !important;
                min-height: 400px !important;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def confirm_destructive_action(action_id: str, action_label: str, action_type: str = "delete") -> bool:
    confirm_key = f"_confirm_{action_id}"
    
    if st.session_state.get(confirm_key):
        col1, col2 = st.columns(2)
        with col1:
            st.warning(f"⚠️ This {action_type} cannot be undone!")
        with col2:
            if st.button("❌ Cancel", key=f"cancel_{action_id}", use_container_width=True):
                st.session_state[confirm_key] = False
                st.rerun()
        
        confirm_col, warning_col = st.columns([1, 1.5])
        with confirm_col:
            if st.button(f"✓ Confirm {action_type.title()}", key=f"confirm_{action_id}", 
                        use_container_width=True, type="primary"):
                st.session_state[confirm_key] = False
                return True
        
        with warning_col:
            st.caption(f"Click 'Confirm {action_type.title()}' to proceed.")
        
        return False
    else:
        st.session_state[confirm_key] = True
        st.warning(f"⚠️ Are you sure you want to {action_type} {action_label}? Click the button again to confirm.")
        st.rerun()


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
    cols = st.columns(4)
    
    # Inject your backend tracking metrics/variables inside the second strings here:
    cols[0].metric("LEAVE DAYS USE", "0")
    cols[1].metric("LOCAL CREDITS", "0")
    cols[2].metric("DIVISION CREDITS", "0")
    cols[3].metric("RELIEVER POINTS", "0")


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
