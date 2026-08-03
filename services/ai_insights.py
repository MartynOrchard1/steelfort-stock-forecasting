"""
AI Insights: sends a compact SUMMARY of the loaded/filtered data to Claude,
not the raw table. A full NetSuite export can be 40,000+ rows, which is far
too much (and too expensive) to hand an LLM directly - so this module
aggregates the important bits first (counts, worst offenders, supplier and
category rollups) and only sends that.
"""

import pandas as pd
import streamlit as st

try:
    import anthropic
except ImportError:
    anthropic = None

MODEL = "claude-sonnet-5"
MAX_ROWS_LISTED = 20


def _fmt_rows(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "(none)"
    cols = [c for c in cols if c in df.columns]
    return df[cols].head(MAX_ROWS_LISTED).to_csv(index=False)


def build_data_summary(df: pd.DataFrame) -> str:
    """
    Build a compact, structured text summary of the currently loaded
    inventory data for the AI to reason over - aggregates and worst-case
    rows only, not the full table.
    """
    if df is None or df.empty:
        return "No inventory data is currently loaded."

    parts = []

    total = len(df)
    priority_counts = df["Priority V2"].value_counts().to_dict() if "Priority V2" in df.columns else {}
    total_backordered = int(df["Back Ordered"].sum()) if "Back Ordered" in df.columns else 0
    total_recommended = int(df["Recommended Order"].sum()) if "Recommended Order" in df.columns else 0

    parts.append(f"Total parts in view: {total:,}")
    parts.append(f"Priority breakdown: {priority_counts}")
    parts.append(f"Total units backordered: {total_backordered:,}")
    parts.append(f"Total units recommended to order: {total_recommended:,}")

    if "Back Ordered" in df.columns:
        worst_backorder = df[df["Back Ordered"] > 0].sort_values("Back Ordered", ascending=False)
        parts.append(f"\nTop backordered items (worst {MAX_ROWS_LISTED}, CSV):")
        parts.append(_fmt_rows(
            worst_backorder,
            ["Part_Number", "Description", "POREF_SUPP", "Qty on hand", "Qty on Order",
             "Back Ordered", "Net After POs", "Priority V2"],
        ))

    if "Recommended Order" in df.columns:
        worst_order = df[df["Recommended Order"] > 0].sort_values("Recommended Order", ascending=False)
        parts.append(f"\nTop items needing reorder (worst {MAX_ROWS_LISTED}, CSV):")
        parts.append(_fmt_rows(
            worst_order,
            ["Part_Number", "Description", "POREF_SUPP", "Qty on hand", "Qty Allocated",
             "Net After POs", "Recommended Order", "Priority V2"],
        ))

    if "POREF_SUPP" in df.columns and "Priority V2" in df.columns:
        supplier_summary = (
            df.groupby("POREF_SUPP", dropna=False)
            .agg(
                Parts=("Part_Number", "count"),
                Urgent=("Priority V2", lambda s: int((s == "🔴 URGENT").sum())),
                Replenish=("Priority V2", lambda s: int((s == "🟡 REPLENISH").sum())),
                Units_To_Order=("Recommended Order", "sum") if "Recommended Order" in df.columns else ("Part_Number", "count"),
            )
            .reset_index()
            .sort_values("Urgent", ascending=False)
        )
        supplier_summary = supplier_summary[supplier_summary["Urgent"] > 0].head(MAX_ROWS_LISTED)
        parts.append("\nSuppliers with the most URGENT items (CSV):")
        parts.append(supplier_summary.to_csv(index=False) if not supplier_summary.empty else "(none)")

    if "Spring Category" in df.columns:
        cat_summary = (
            df.groupby("Spring Category", dropna=False)
            .agg(
                Parts=("Part_Number", "count"),
                Urgent=("Priority V2", lambda s: int((s == "🔴 URGENT").sum())) if "Priority V2" in df.columns else ("Part_Number", "count"),
                Back_Ordered_Units=("Back Ordered", "sum") if "Back Ordered" in df.columns else ("Part_Number", "count"),
            )
            .reset_index()
            .sort_values("Urgent", ascending=False)
        )
        parts.append("\nSpring Category rollup (CSV):")
        parts.append(cat_summary.to_csv(index=False))

    return "\n".join(parts)


SYSTEM_PROMPT = """You are a purchasing/inventory assistant for Steelfort, a company that does spare parts \
purchasing (not whole units) and manages a Cutting Edge (POC) product line assembled from imported components. \
You're given a summarised snapshot of their NetSuite-derived reorder data - aggregate counts and the worst-case \
rows, not the full dataset. Priority meanings: URGENT = a real shortfall (backordered stock or negative net \
availability, order now), REPLENISH = below normal reorder point but not critical, OK = fine.

Be concise, direct, and practical - this is read by a purchasing person deciding what to order today, not a \
report for executives. Use plain language, short paragraphs or a tight bullet list, no fluff. When asked for a \
summary, lead with the most urgent/actionable items. When asked to spot anomalies, look for things like: a \
single supplier with an unusually high share of urgent items, backorders far larger than everything else, or \
categories that stand out. If the data doesn't support an answer, say so rather than guessing."""


def get_client():
    api_key = st.secrets.get("ANTHROPIC_API_KEY")
    if not api_key:
        return None
    if anthropic is None:
        return None
    return anthropic.Anthropic(api_key=api_key)


MAX_HISTORY_TURNS = 6  # keep the last 3 exchanges - bounds cost as a chat grows


def ask_ai(data_summary: str, conversation: list[dict], user_message: str) -> str:
    """
    conversation: list of {"role": "user"|"assistant", "content": str} from
    prior turns in this session. Only the most recent turns are kept (each
    call re-embeds the full data summary, so an unbounded history would
    make every question progressively more expensive) and the summary is
    rebuilt fresh every call so it can't go stale between questions.
    """
    client = get_client()
    if client is None:
        return (
            "No Anthropic API key configured. Add ANTHROPIC_API_KEY in "
            "Secrets (Streamlit Cloud: Settings > Secrets, or locally in "
            ".streamlit/secrets.toml) to enable AI Insights."
        )

    trimmed_history = conversation[-MAX_HISTORY_TURNS:]
    # Anthropic requires the message list to start with a "user" turn -
    # drop a stray leading assistant turn if the trim cut mid-exchange.
    while trimmed_history and trimmed_history[0]["role"] != "user":
        trimmed_history = trimmed_history[1:]

    messages = []
    for turn in trimmed_history:
        messages.append({"role": turn["role"], "content": turn["content"]})

    messages.append({
        "role": "user",
        "content": f"Here is the current data snapshot:\n\n{data_summary}\n\nQuestion: {user_message}",
    })

    try:
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=messages,
        )
        return "".join(block.text for block in response.content if hasattr(block, "text"))
    except Exception as e:
        return f"AI request failed: {e}"
