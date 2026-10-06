import pandas as pd
import streamlit as st

import aged_stock
from services.aged_ai import (
    AGED_SUMMARY_REQUEST,
    AGED_SYSTEM_PROMPT,
    build_aged_summary,
    clearance_lines,
    find_replacements,
    triage_clearance,
    write_promo_copy,
)
from services.file_loader import load_file_from_bytes
from ui.ai_insights_view import render_ai_insights


def _run(label: str, state_key: str, fn) -> None:
    """Run a billed AI call on click and keep the result for later reruns."""
    try:
        with st.spinner(f"{label}..."):
            st.session_state[state_key] = fn()
    except Exception as e:
        st.error(f"AI request failed: {e}")


def _show(state_key: str, file_name: str) -> None:
    result = st.session_state.get(state_key)
    if result is not None:
        st.dataframe(result, width="stretch", hide_index=True)
        st.download_button("Download CSV", result.to_csv(index=False).encode("utf-8"),
                           file_name, "text/csv", key=f"{state_key}_dl")


def _render_promo_copy(aged_df: pd.DataFrame) -> None:
    with st.expander("✍️ Portal specials promo copy"):
        st.caption("Turns the portal specials list above into customer-facing titles and blurbs. "
                   "Check the copy before it goes live.")
        ss = st.session_state
        portal = aged_stock.portal_list(aged_df, ss.get("aged_top", 30), ss.get("aged_dr", 15),
                                        ss.get("aged_dc", 30), aged_df["Unit_Cost"].notna().any())
        if st.button(f"Write promo copy for {len(portal)} lines", key="aged_promo_btn", disabled=portal.empty):
            _run("Writing promo copy", "aged_ai_promo", lambda: write_promo_copy(portal))
        _show("aged_ai_promo", "portal_specials_promo_copy.csv")


def _read_raw(uploaded_file) -> pd.DataFrame | None:
    return load_file_from_bytes(uploaded_file.getvalue(), uploaded_file.name) if uploaded_file else None


def _render_triage(aged_df: pd.DataFrame, reorder_file) -> None:
    with st.expander("🧹 Clearance triage"):
        st.caption("Suggests an action for the highest-value Clearance lines: discount, bundle, return to "
                   "supplier, write off, or keep as an insurance spare. Suggestions to review, not decisions.")
        top_n = st.slider("Lines to triage (highest value first)", 50, 500, 200, 50, key="aged_triage_n")
        if st.button(f"Triage top {top_n} Clearance lines", key="aged_triage_btn"):
            lines = clearance_lines(aged_df, _read_raw(reorder_file), top_n)
            _run(f"Triaging {len(lines)} lines", "aged_ai_triage", lambda: triage_clearance(lines))
        result = st.session_state.get("aged_ai_triage")
        if result is not None and "Stock_Value" in result:
            st.dataframe(result.groupby("Suggested_Action", dropna=False)
                         .agg(Lines=("Part_Number", "size"), Value=("Stock_Value", "sum")).round(0),
                         width="content")
        _show("aged_ai_triage", "clearance_triage.csv")


def _render_replacements(aged_df: pd.DataFrame, reorder_file, tims_csv, ns_csv) -> None:
    with st.expander("🔁 Replacement parts for superseded stock"):
        st.caption("Finds the replacement each aged line's description or item notes point to (REF / TRY / "
                   "SUPERSEDED BY / REPLACED BY...), asks Claude only about the ones a pattern can't resolve, then "
                   "checks whether the replacement is selling - if it is, sell the old stock against its demand. "
                   "Needs_REF lists parts to REF over to the new part number in NetSuite.")
        if reorder_file is None or tims_csv is None or ns_csv is None:
            st.info("Needs the reorder report plus the TIMS and NetSuite sales history CSVs.")
            return
        if st.button("Find replacement parts", key="aged_repl_btn"):
            ss = st.session_state
            _run("Finding replacements", "aged_ai_repl", lambda: find_replacements(
                aged_df, _read_raw(reorder_file), pd.read_csv(tims_csv, dtype=str), pd.read_csv(ns_csv, dtype=str),
                ss["aged_asat"], ss["aged_tl"], ss.get("aged_rv", 6)))
        _show("aged_ai_repl", "superseded_replacements.csv")


def render_aged_ai(aged_df: pd.DataFrame, reorder_file=None, tims_csv=None, ns_csv=None) -> None:
    """
    AI tools for the Aged Stock tab. Every button is a deliberate, billed request.
    reorder_file: the uploaded Part Reorder Rpt (for supplier / item-list lookups).
    tims_csv / ns_csv: file-likes of the TIMS and NetSuite sales history CSVs.
    """
    st.divider()
    st.markdown("### 🤖 AI Tools")
    _render_promo_copy(aged_df)
    _render_triage(aged_df, reorder_file)
    _render_replacements(aged_df, reorder_file, tims_csv, ns_csv)
    render_ai_insights(
        aged_df,
        key_prefix="aged_",
        summary_fn=build_aged_summary,
        system_prompt=AGED_SYSTEM_PROMPT,
        summary_request=AGED_SUMMARY_REQUEST,
    )
