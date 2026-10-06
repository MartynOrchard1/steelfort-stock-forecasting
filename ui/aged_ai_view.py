import pandas as pd
import streamlit as st

import aged_stock
from services.aged_ai import (
    AGED_SUMMARY_REQUEST,
    AGED_SYSTEM_PROMPT,
    build_aged_summary,
    write_promo_copy,
)
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


def render_aged_ai(aged_df: pd.DataFrame) -> None:
    """AI tools for the Aged Stock tab. Every button is a deliberate, billed request."""
    st.divider()
    st.markdown("### 🤖 AI Tools")
    _render_promo_copy(aged_df)
    render_ai_insights(
        aged_df,
        key_prefix="aged_",
        summary_fn=build_aged_summary,
        system_prompt=AGED_SYSTEM_PROMPT,
        summary_request=AGED_SUMMARY_REQUEST,
    )
