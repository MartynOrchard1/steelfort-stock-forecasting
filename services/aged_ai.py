"""
AI helpers for the Aged Stock tab. Same rules as ai_insights.py: Claude only
ever sees a compact summary or a short list of lines (never the full
15,000-line aged table), and nothing runs until someone clicks a button.
"""

import pandas as pd

import aged_stock

MAX_ROWS_LISTED = 20
AGED_COLS = ["Part_Number", "Description", "Part Group", "Qty_On_Hand", "Stock_Value",
             "Months_Since_Move", "Bucket", "Flags"]


def _value_col(df: pd.DataFrame) -> str:
    # The reorder report may not carry a cost column - fall back to quantities.
    return "Stock_Value" if df["Stock_Value"].notna().any() else "Qty_On_Hand"


def _top(df: pd.DataFrame, cols: list[str]) -> str:
    if df.empty:
        return "(none)"
    return df.sort_values(_value_col(df), ascending=False).head(MAX_ROWS_LISTED)[cols].round(2).to_csv(index=False)


def build_aged_summary(df: pd.DataFrame) -> str:
    if df is None or df.empty:
        return "No aged stock data is currently loaded."

    aged = df[df["Bucket"].isin(["Review", "Clearance"])]
    parts = ["Bucket summary (CSV - Lines, Qty on hand, Stock Value $):",
             aged_stock.summary(df).round(0).to_csv()]

    by_group = (
        aged.pivot_table(index="Part Group", columns="Bucket", values=_value_col(df),
                         aggfunc="sum", fill_value=0)
        .assign(Lines=aged.groupby("Part Group").size())
    )
    sort_by = "Clearance" if "Clearance" in by_group else by_group.columns[0]
    parts.append(f"\nPart groups with the most aged {_value_col(df)} (top {MAX_ROWS_LISTED}, CSV):")
    parts.append(by_group.sort_values(sort_by, ascending=False).head(MAX_ROWS_LISTED).round(0).to_csv())

    parts.append(f"\nBiggest aged lines (top {MAX_ROWS_LISTED}, CSV):")
    parts.append(_top(aged, AGED_COLS))

    conflicts = aged[aged["Conflict"]]
    parts.append(f"\nAged lines still committed / on order / back ordered: {len(conflicts):,} lines. Biggest (CSV):")
    parts.append(_top(conflicts, AGED_COLS[:2] + ["Committed", "On_Order", "Back_Ordered", "Stock_Value", "Bucket"]))

    auto = aged[aged["Flags"].str.contains("auto-reorder")]
    parts.append(f"\nAged lines with a reorder point set (will auto-reorder): {len(auto):,} lines. Biggest (CSV):")
    parts.append(_top(auto, AGED_COLS[:2] + ["Reorder_Point", "Preferred_Level", "Stock_Value", "Bucket"]))

    parts.append(f"\nLines needing a data check (not in TIMS, no NetSuite sale): {int((df['Bucket'] == 'Check data').sum()):,}")
    return "\n".join(parts)


AGED_SYSTEM_PROMPT = """You are an inventory assistant for Steelfort's spare parts department (location 10). \
You're given a summarised snapshot of their aged stock analysis - aggregates and the biggest lines, not the full \
dataset. Age is months since the part last moved: the latest of its last NetSuite sale and its last month with \
positive TIMS movement (TIMS movement may include transfers, so it can understate age). Buckets: Active = moved in \
the last 6 months; Review = 6-12 months, candidate for a portal special or flyer; Clearance = 12+ months, \
automatically set to DO NOT ORDER in purchasing; Check data = no history anywhere (possibly a new item). \
"Conflict" lines are aged but still committed, on order or back ordered - worth checking before discounting. Lines \
with a reorder point will keep being reordered by NetSuite until the reorder point is removed.

Be concise, direct, and practical - this is read by the person deciding what to discount, clear or stop \
reordering. Lead with where the money is tied up and the highest-impact actions. Use plain language and a tight \
bullet list. If the data doesn't support an answer, say so rather than guessing.

Keep your ENTIRE response under about 400 words. If there's more worth flagging than fits, say so explicitly \
rather than trying to cram everything in."""

AGED_SUMMARY_REQUEST = (
    "Summarise where aged stock money is tied up and what to act on first: the biggest "
    "concentrations by part group, aged lines still on order or set to auto-reorder, and "
    "anything that looks odd or worth a second look."
)
