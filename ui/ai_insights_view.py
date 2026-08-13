import pandas as pd
import streamlit as st

from services.ai_insights import ask_ai, build_data_summary


def render_ai_insights(df: pd.DataFrame, key_prefix: str = "") -> None:
    """
    AI Insights panel: an on-demand summary/anomaly button plus a small
    follow-up chat, both grounded in a compact summary of the currently
    filtered table (not the raw 40,000+ row export - see ai_insights.py).

    Nothing calls the API automatically. Every button press and message
    sent is a deliberate, billed request - this stays opt-in rather than
    running on every page load.

    key_prefix: pass a unique prefix when this panel is rendered more than
    once on the same page (e.g. one per tab in Units Ordering) - keeps each
    instance's widget keys and chat history independent so Streamlit
    doesn't collide their auto-generated element IDs.
    """
    st.markdown("### 🤖 AI Insights")

    history_key = f"{key_prefix}ai_chat_history"
    if history_key not in st.session_state:
        st.session_state[history_key] = []

    col1, col2 = st.columns([1, 1])

    if col1.button(
        "Generate summary & flag anomalies",
        use_container_width=True,
        key=f"{key_prefix}ai_generate_summary",
    ):
        with st.spinner("Asking Claude..."):
            summary = build_data_summary(df)
            answer = ask_ai(
                summary,
                st.session_state[history_key],
                "Give me a short summary of what needs attention today, then "
                "call out anything that looks like an anomaly or worth a "
                "second look (e.g. a supplier with an unusual share of "
                "urgent items, an unusually large backorder, etc).",
            )
        st.session_state[history_key].append({
            "role": "user", "content": "Summarize and flag anomalies.",
        })
        st.session_state[history_key].append({
            "role": "assistant", "content": answer,
        })

    if col2.button("Clear chat", use_container_width=True, key=f"{key_prefix}ai_clear_chat"):
        st.session_state[history_key] = []

    for turn in st.session_state[history_key]:
        with st.chat_message(turn["role"]):
            st.write(turn["content"])

    user_question = st.chat_input(
        "Ask a question about the currently loaded data...",
        key=f"{key_prefix}ai_chat_input",
    )
    if user_question:
        with st.chat_message("user"):
            st.write(user_question)
        with st.spinner("Asking Claude..."):
            summary = build_data_summary(df)
            answer = ask_ai(summary, st.session_state[history_key], user_question)
        st.session_state[history_key].append({"role": "user", "content": user_question})
        st.session_state[history_key].append({"role": "assistant", "content": answer})
        with st.chat_message("assistant"):
            st.write(answer)
