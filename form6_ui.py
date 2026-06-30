from __future__ import annotations

from datetime import date, datetime
from io import BytesIO

import pandas as pd
import streamlit as st
from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# --- EXPORT STYLING CONSTANTS ---
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
        @import url('https://fonts.googleapis.com/css2?family=Google+Sans:wght@400;500;600;700&family=Inter:wght@400;500;600&display=swap');

        /* =========================================================
           1. GOOGLE MATERIAL DESIGN BASE (GLOBAL)
           ========================================================= */
        html, body, [class*="css"] {
            font-family: 'Google Sans', 'Inter', -apple-system, sans-serif !important;
            color: #E8EAED !important;
        }
        
        .block-container { 
            padding-top: 1.5rem; 
            padding-bottom: 2rem; 
            max-width: 1200px;
        }

        header { background: transparent !important; }

        h1, h2, h3, h4, h5, h6 { 
            font-family: 'Google Sans', sans-serif !important;
            font-weight: 600 !important;
            color: #FFFFFF !important;
            letter-spacing: -0.01em !important;
        }

        /* Material Buttons */
        .stButton > button {
            background-color: #8AB4F8 !important;
            color: #121212 !important;
            border: none !important;
            border-radius: 24px !important;
            padding: 10px 24px !important;
            font-weight: 600 !important;
            font-size: 0.95rem !important;
            transition: all 0.2s ease !important;
            min-height: 48px !important;
        }
        .stButton > button:hover { 
            background-color: #AECBFA !important; 
            box-shadow: 0 1px 3px rgba(0,0,0,0.4) !important;
        }

        /* Input Fields */
        div[data-baseweb="select"] > div, 
        div[data-baseweb="input"] > div {
            background-color: #1E1E1E !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            border-radius: 8px !important;
        }

        /* =========================================================
           2. MATERIAL CARDS (METRICS & FORMS)
           ========================================================= */
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) {
            display: grid !important;
            grid-template-columns: repeat(4, 1fr) !important;
            gap: 16px !important;
            width: 100% !important;
        }
        
        div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) > div[data-testid="column"] {
            width: 100% !important;
            min-width: 100% !important; 
            display: flex !important;
        }

        div[data-testid="stMetric"], 
        [data-testid="stForm"],
        [data-testid="stDataFrame"] {
            background-color: #1E1E1E !important;
            border: 1px solid rgba(255, 255, 255, 0.12) !important;
            border-radius: 12px !important;
            box-shadow: 0 2px 4px rgba(0, 0, 0, 0.2) !important;
            width: 100% !important; 
        }

        div[data-testid="stMetric"] {
            padding: 16px !important;
            display: flex !important;
            flex-direction: column !important;
            align-items: flex-start !important;
            justify-content: center !important;
        }

        div[data-testid="stMetricValue"] {
            color: #8AB4F8 !important;
            font-size: 2rem !important;
            font-weight: 500 !important;
            line-height: 1.1 !important;
            font-family: 'Google Sans', sans-serif !important;
        }

        div[data-testid="stMetricLabel"] { 
            color: #9AA0A6 !important;
            font-size: 0.85rem !important; 
            font-weight: 500 !important; 
            text-transform: uppercase !important;
            letter-spacing: 0.05em !important;
            margin-bottom: 8px !important;
        }

        /* =========================================================
           3. TABS & DATA TABLES (MD3 STYLE)
           ========================================================= */
        .stTabs [data-baseweb="tab-list"] {
            gap: 0px !important; 
            border-bottom: 1px solid rgba(255, 255, 255, 0.12) !important; 
            padding-bottom: 0 !important;
        }
        .stTabs [data-baseweb="tab"] {
            height: 48px; 
            background-color: transparent !important; 
            border-radius: 0 !important; 
            font-weight: 500 !important;
            color: #9AA0A6 !important;
            padding: 0 16px !important;
            margin: 0 !important;
            border-bottom: 3px solid transparent !important;
        }
        .stTabs [aria-selected="true"] { 
            color: #8AB4F8 !important; 
            border-bottom: 3px solid #8AB4F8 !important; 
        }

        /* =========================================================
           4. MOBILE-FIRST RESPONSIVENESS (< 768px)
           ========================================================= */
        @media screen and (max-width: 768px) {
            
            .block-container {
                padding-top: 1rem !important;
                padding-left: 12px !important;
                padding-right: 12px !important;
                padding-bottom: 5rem !important; 
            }

            h1, [data-testid="stHeadingContainer"] h1 {
                font-size: 1.7rem !important; 
                line-height: 1.3 !important;
                word-wrap: break-word !important;
                white-space: normal !important; 
            }

            div[data-testid="stHorizontalBlock"]:has(div[data-testid="stMetric"]) {
                grid-template-columns: repeat(2, 1fr) !important;
                gap: 12px !important;
            }

            div[data-testid="stHorizontalBlock"]:not(:has(div[data-testid="stMetric"])) {
                flex-direction: column !important;
            }
            div[data-testid="stHorizontalBlock"]:not(:has(div[data-testid="stMetric"])) > div[data-testid="column"] {
                width: 100% !important;
                min-width: 100% !important;
            }

            div[data-testid="stHtml"] {
                height: auto !important;
                min-height: max-content !important;
                margin-top: -10px !important;
                padding: 0 !important;
                width: 100% !important;
                overflow: hidden !important;
                display: flex !important;
                justify-content: center !important;
            }
            
            iframe[title*="calendar" i], .fb-cover-calendar iframe {
                width: 100% !important;
                min-width: 100% !important;
                height: 400px !important;
                border: none !important;
            }

            [data-testid="stVerticalBlock"] > div { 
                padding-bottom: 0 !important; 
                margin-bottom: 8px !important;
            }

            .stTabs [data-baseweb="tab-list"] {
                display: flex !important;
                justify-content: space-between !important;
                overflow-x: auto !important;
                scrollbar-width: none !important;
            }
            .stTabs [data-baseweb="tab"] {
                flex: 1 !important;
                text-align: center !important;
                font-size: 0.85rem !important;
                min-width: max-content !important;
            }

            [data-testid="stDataFrame"] {
                padding: 0 !important;
                border: none !important;
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
    try: return st.tabs(labels, key=session_key)
    except TypeError: return st.tabs(labels)

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
