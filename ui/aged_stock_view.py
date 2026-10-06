import io

import streamlit as st

import aged_stock
from ui.aged_ai_view import render_aged_ai


def _fresh_copy(uploaded_file):
    """aged_stock.render has already read the upload - give the AI tools their own copy."""
    return io.BytesIO(uploaded_file.getvalue()) if uploaded_file is not None else None


def render_aged_stock_mode() -> None:
    """
    Aged stock for location 10, with its own uploads (reorder report, TIMS
    history, NetSuite sales). The result stays in st.session_state["aged_df"]
    so Spare Parts Ordering can apply DO NOT ORDER to Clearance parts.
    """
    try:
        aged_stock.render()
    except Exception as e:
        st.session_state.pop("aged_df", None)
        st.error(f"Aged stock couldn't run on these files: {e}")

    ss = st.session_state
    if "aged_df" in ss:
        render_aged_ai(ss["aged_df"], ss.get("aged_reorder"),
                       _fresh_copy(ss.get("aged_tims")), _fresh_copy(ss.get("aged_ns")))
