"""
"Import / Backup" tab: database backup download, plus the PROTOTYPE
biometrics (lates/absents) import.

See form6_biometrics.py for what's placeholder vs. final in this feature.
"""

from __future__ import annotations

import streamlit as st

from form6_actions import clear_streamlit_cache, ensure_connection
from form6_biometrics import build_import_preview, commit_import, read_biometrics_excel
from form6_settings import get_biometrics_settings
from form6_ui import db_backup_bytes, flash


def render(employees_all, leave_all) -> None:
    st.subheader("Database Backups")
    if st.button("Generate database backup", key="gen_db_backup_tab", use_container_width=True):
        st.session_state["_db_backup_bytes_tab"] = db_backup_bytes()
    if "_db_backup_bytes_tab" in st.session_state:
        st.download_button("Download database backup", data=st.session_state["_db_backup_bytes_tab"], file_name="form6_tracker.sqlite3", mime="application/octet-stream", use_container_width=True, key="dl_db_backup_tab")

    st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)
    st.subheader("Biometrics Import (Lates & Absents)")
    settings = get_biometrics_settings()
    st.warning(
        "**Prototype feature.** The Excel column layout and the deduction amounts below are "
        f"placeholders (Absent = {settings['absent_deduction_days']:.2f} credit, Late = {settings['late_deduction_days']:.2f} credit, flat). "
        "Nothing is deducted until you review the preview and click Confirm.",
        icon="🧪",
    )

    uploaded_file = st.file_uploader("Upload biometrics Excel file", type=["xlsx", "xls"], key="biometrics_upload")
    if uploaded_file is None:
        return

    df, warnings = read_biometrics_excel(uploaded_file)
    for warning_msg in warnings:
        st.error(warning_msg)
    if warnings:
        st.info("Fix the column names in the Excel file (or update EXPECTED_COLUMNS in form6_biometrics.py) and re-upload.")
        return

    preview = build_import_preview(
        df, employees_all, leave_all,
        absent_deduction_days=settings["absent_deduction_days"],
        late_deduction_days=settings["late_deduction_days"],
    )

    st.markdown("#### Preview")
    p_cols = st.columns(5)
    p_cols[0].metric("Matched rows", len(preview.matched))
    p_cols[1].metric("Unmatched names", len(preview.unmatched))
    p_cols[2].metric("Leave conflicts", len(preview.conflicts))
    p_cols[3].metric("Already imported", len(preview.already_imported))
    p_cols[4].metric("Skipped", preview.skipped_rows)

    if preview.already_imported:
        with st.expander(f"ℹ️ {len(preview.already_imported)} row(s) already imported — will be skipped", expanded=False):
            for row in preview.already_imported[:50]:
                st.write(f"- **{row.matched_label}** — {row.date_iso or 'no date'} — {row.status}")
            if len(preview.already_imported) > 50:
                st.caption(f"...and {len(preview.already_imported) - 50} more.")

    if preview.conflicts:
        with st.expander(f"🛑 {len(preview.conflicts)} row(s) overlap an existing approved leave - NOT imported", expanded=True):
            st.caption(
                "These dates already have a leave record on file for this employee (e.g. they were on approved "
                "leave, so the biometrics device correctly shows no punch). Deducting credit here would be wrong, "
                "so these are excluded automatically. If any of these look incorrect, check the employee's Leave "
                "Ledger entry for that date."
            )
            for row in preview.conflicts[:50]:
                st.write(f"- **{row.matched_label}** — {row.date_iso or 'no date'} — {row.status}")
            if len(preview.conflicts) > 50:
                st.caption(f"...and {len(preview.conflicts) - 50} more.")

    if preview.unmatched:
        with st.expander(f"⚠️ {len(preview.unmatched)} row(s) could not be matched to an employee", expanded=True):
            st.caption("These rows will NOT be imported. Check spelling against the employee's name on file.")
            for row in preview.unmatched[:50]:
                st.write(f"- **{row.raw_name}** — {row.date_iso or 'no date'} — {row.status}")
            if len(preview.unmatched) > 50:
                st.caption(f"...and {len(preview.unmatched) - 50} more.")

    if preview.matched:
        with st.expander(f"{len(preview.matched)} row(s) ready to import", expanded=True):
            h_cols = st.columns([0.30, 0.20, 0.20, 0.30])
            h_cols[0].markdown("**Employee**")
            h_cols[1].markdown("**Date**")
            h_cols[2].markdown("**Type**")
            h_cols[3].markdown("**Deduction**")
            st.divider()
            for row in preview.matched[:50]:
                r_cols = st.columns([0.30, 0.20, 0.20, 0.30])
                r_cols[0].write(row.matched_label or "")
                r_cols[1].write(row.date_iso or "-")
                r_cols[2].write(row.status)
                r_cols[3].write(f"-{row.deduction_days:.2f}")
            if len(preview.matched) > 50:
                st.caption(f"...and {len(preview.matched) - 50} more row(s) not shown, but will be imported.")

        st.markdown("#### Confirm import")
        credit_scope = st.selectbox(
            "Deduct from which credit scope?",
            ["LOCAL", "DIVISION"],
            key="biometrics_scope",
            help="Placeholder: every matched row is deducted from this single scope. A real policy may need per-row scope logic instead.",
        )
        if st.button(f"Confirm: deduct {len(preview.matched)} row(s) from {credit_scope}", key="biometrics_confirm", use_container_width=True):
            with ensure_connection() as conn:
                inserted = commit_import(conn, preview, credit_scope, source_ref=uploaded_file.name)
            clear_streamlit_cache()
            flash(f"Imported {inserted} biometrics deduction(s) against {credit_scope} credit.")
            st.rerun()
