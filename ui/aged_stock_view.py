import streamlit as st

import aged_stock
from ui.aged_ai_view import render_aged_ai


def render_aged_stock_mode() -> None:
    """
    Aged stock for location 10, with its own uploads (reorder report, NetSuite
    sales, TIMS last usage, optional NetSuite receipts). The result stays in
    st.session_state["aged_df"] so Spare Parts Ordering can apply DO NOT ORDER
    to Clearance parts.
    """
    try:
        aged_stock.render()
    except Exception as e:
        st.session_state.pop("aged_df", None)
        st.error(f"Aged stock couldn't run on these files: {e}")

    ss = st.session_state
    if "aged_df" in ss:
        # AI tools follow the Filters panel; the DO NOT ORDER override keeps using the full aged_df.
        render_aged_ai(ss.get("aged_view", ss["aged_df"]), ss.get("aged_reorder"),
                       ss.get("aged_tims_usage"), ss.get("aged_ns"))
