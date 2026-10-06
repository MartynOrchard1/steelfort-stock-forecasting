import pandas as pd
import streamlit as st

from services.aged_ai import AGED_SUMMARY_REQUEST, AGED_SYSTEM_PROMPT, build_aged_summary
from ui.ai_insights_view import render_ai_insights


def render_aged_ai(aged_df: pd.DataFrame) -> None:
    """AI tools for the Aged Stock tab. Every button is a deliberate, billed request."""
    st.divider()
    render_ai_insights(
        aged_df,
        key_prefix="aged_",
        summary_fn=build_aged_summary,
        system_prompt=AGED_SYSTEM_PROMPT,
        summary_request=AGED_SUMMARY_REQUEST,
    )
