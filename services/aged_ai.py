"""
AI helpers for the Aged Stock tab. Same rules as ai_insights.py: Claude only
ever sees a compact summary or a short list of lines (never the full
15,000-line aged table), and nothing runs until someone clicks a button.
"""

import difflib
import re
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

import numpy as np
import pandas as pd
from pydantic import BaseModel

import aged_stock
from services.ai_insights import MODEL, get_client

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


# ---------------------------------------------------------------------------
# Structured-output calls (promo copy, triage, replacements)
# ---------------------------------------------------------------------------
NO_KEY = (
    "No Anthropic API key configured. Add ANTHROPIC_API_KEY in Secrets (Streamlit Cloud: "
    "Settings > Secrets, or locally in .streamlit/secrets.toml)."
)


def require_client():
    client = get_client()
    if client is None:
        raise RuntimeError(NO_KEY)
    return client


def _parse(client, system: str, content: str, schema: type[BaseModel]) -> BaseModel:
    """One Claude call whose reply is validated against `schema`."""
    response = client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": content}],
        output_format=schema,
    )
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Claude's reply was cut off - try fewer lines.")
    if response.parsed_output is None:
        raise RuntimeError(f"Claude didn't return a usable answer (stop reason: {response.stop_reason}).")
    return response.parsed_output


def _merge_by_part(lines: pd.DataFrame, results: list[BaseModel], columns: dict[str, str]) -> pd.DataFrame:
    """Join per-part AI results back onto `lines`; columns maps result field -> output column."""
    out = pd.DataFrame([r.model_dump() for r in results], columns=["part_number", *columns])
    out = out.drop_duplicates("part_number").rename(columns={"part_number": "Part_Number", **columns})
    return lines.merge(out, on="Part_Number", how="left")


class PromoLine(BaseModel):
    part_number: str
    title: str
    blurb: str
    flyer_theme: str


class PromoCopy(BaseModel):
    lines: list[PromoLine]


PROMO_PROMPT = """You write short customer-facing promo copy for Steelfort's online parts portal and flyers (outdoor power equipment, mower and appliance spare parts). You get internal part descriptions, which are terse and abbreviated (e.g. "WASHER FLAT ZP 1/2X1-1/8X10G *").

For every line, return the part_number exactly as given, plus:
- title: a clear product name a customer would search for, max 60 characters. Expand abbreviations you're sure of (ZP = zinc plated, ASSY = assembly, LH/RH = left/right hand). Keep sizes and model numbers exactly as written.
- blurb: one sentence, max 120 characters, saying what it is and what it fits if the description says so.
- flyer_theme: a short group name so related lines can share a flyer section (e.g. "Mower blades", "Engine oil").

Only use facts in the description - never invent specs, brands, compatibility or prices. If a description is too cryptic to expand safely, keep the title close to the original wording. Ignore trailing asterisks."""


def write_promo_copy(portal: pd.DataFrame) -> pd.DataFrame:
    """Add Promo_Title / Promo_Blurb / Flyer_Theme to the portal specials list."""
    content = portal[["Part_Number", "Description", "Bucket"]].to_csv(index=False)
    result = _parse(require_client(), PROMO_PROMPT, content, PromoCopy)
    return _merge_by_part(portal, result.lines, {
        "title": "Promo_Title", "blurb": "Promo_Blurb", "flyer_theme": "Flyer_Theme",
    })


class TriageLine(BaseModel):
    part_number: str
    action: Literal["Discount / portal special", "Bundle with related parts", "Return to supplier",
                    "Write off / scrap", "Keep as insurance spare"]
    reason: str


class Triage(BaseModel):
    lines: list[TriageLine]


TRIAGE_PROMPT = """You help Steelfort's spare parts department (outdoor power equipment, mower and appliance parts) decide what to do with Clearance stock: parts on hand that haven't moved in 12+ months. For every line, return the part_number exactly as given, one action, and a reason of one short sentence (max 20 words):
- "Discount / portal special": a part customers still buy, just slowly - price it to move.
- "Bundle with related parts": cheap, small or slow on its own but sells alongside other parts (e.g. washers, bolts, belts with pulleys).
- "Return to supplier": high value, likely still current with the supplier, worth asking for a credit.
- "Write off / scrap": obsolete, superseded, damaged-sounding, or worth too little to handle.
- "Keep as insurance spare": slow but critical - a customer would be stuck without it and it's hard to get quickly (e.g. engine, transmission, steering or electronic control parts for machines still in use).

Base it only on the line's description, part group, supplier, quantity, value and age. Months_Since_Move is a minimum when Flags says so. Prefer "Keep as insurance spare" only when the part is genuinely critical, not just expensive."""

CHUNK = 50  # lines per request - keeps each reply well under max_tokens
TRIAGE_COLS = ["Part_Number", "Description", "Part Group", "Supplier", "Qty_On_Hand", "Unit_Cost",
               "Stock_Value", "Months_Since_Move", "Flags"]


def clearance_lines(aged_df: pd.DataFrame, reorder: pd.DataFrame | None, top_n: int) -> pd.DataFrame:
    """The top_n Clearance lines by value, with supplier from the raw reorder report when available."""
    lines = aged_df[aged_df["Bucket"] == "Clearance"].sort_values(_value_col(aged_df), ascending=False).head(top_n)
    supplier = {}
    if reorder is not None and "Supplier" in reorder.columns:
        r = reorder.assign(Part_Number=reorder["Part_Number"].astype(str).str.strip()).drop_duplicates("Part_Number")
        supplier = dict(zip(r["Part_Number"], r["Supplier"].fillna("")))  # first supplier row, as aged_stock does
    return lines.assign(Supplier=lines["Part_Number"].map(supplier).fillna(""))[TRIAGE_COLS].round(2)


def _parse_chunked(system: str, lines: pd.DataFrame, schema: type[BaseModel]) -> list:
    """Send `lines` as CSV in chunks of CHUNK, four requests at a time; returns every chunk's .lines."""
    client = require_client()  # resolved once here: st.secrets isn't for worker threads
    chunks = [lines.iloc[i:i + CHUNK].to_csv(index=False) for i in range(0, len(lines), CHUNK)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = pool.map(lambda c: _parse(client, system, c, schema).lines, chunks)
        return [line for chunk in results for line in chunk]


def triage_clearance(lines: pd.DataFrame) -> pd.DataFrame:
    """Add Suggested_Action / Action_Reason to clearance_lines() output."""
    triaged = _parse_chunked(TRIAGE_PROMPT, lines, Triage)
    return _merge_by_part(lines, triaged, {"action": "Suggested_Action", "reason": "Action_Reason"})


# Descriptions name replacements like "REF PPBB043", "(TRY 618P09970)*", "REPLACED BY MT9420616A".
REPLACEMENT_HINT = re.compile(r"\b(?:REF|TRY|REPLACED BY|NOW)\b", re.I)
REPLACEMENT_REF = re.compile(r"\b(?:REF:?|TRY|REPLACED BY|NOW)\s+([A-Z0-9][A-Z0-9\-/.]*\d[A-Z0-9\-/.]*)", re.I)
REPLACEMENT_COLS = ["Part_Number", "Description", "Bucket", "Qty_On_Hand", "Stock_Value", "Months_Since_Move"]


def superseded_lines(aged_df: pd.DataFrame, known: dict[str, str]) -> pd.DataFrame:
    """
    Aged lines whose description points at a replacement (or that aged_stock flags as superseded),
    with Replacement filled in where a pattern finds a part number that exists in the item list.
    known: upper-cased part number -> part number as spelled in the reorder report.
    """
    aged = aged_df[aged_df["Bucket"].isin(["Review", "Clearance"])]
    lines = aged[aged["Description"].fillna("").str.contains(REPLACEMENT_HINT)
                 | aged["Flags"].str.contains("superseded")][REPLACEMENT_COLS].copy()
    lines["Ref_Token"] = (lines["Description"].fillna("").str.extract(REPLACEMENT_REF, expand=False)
                          .fillna("").str.upper().str.rstrip("./-"))
    lines["Replacement"] = lines["Ref_Token"].map(known).fillna("")
    lines["Found_By"] = np.where(lines["Replacement"] != "", "Description", "")

    # Descriptions often drop the item prefix ("REF 13120-004-0000" means MT13120-004-0000):
    # take a suffix match when exactly one item in the list ends with the token.
    for i in lines.index[(lines["Found_By"] == "") & (lines["Ref_Token"] != "")]:
        matches = _ending_with(lines.at[i, "Ref_Token"], known)
        if len(matches) == 1:
            lines.loc[i, ["Replacement", "Found_By"]] = [matches[0], "Description (prefix added)"]
    return lines


class ReplacementLine(BaseModel):
    part_number: str
    replacement: str


class Replacements(BaseModel):
    lines: list[ReplacementLine]


REPLACEMENT_PROMPT = """You read terse spare-part descriptions from Steelfort's item list and find the \
replacement (superseding) part number each one points to, e.g. "REF PPBB043" -> PPBB043, or "TRY -805" on old part \
MT717-04110 with similar part MT717-0805 listed -> MT717-0805. For every line return the part_number exactly as \
given and replacement: the replacement part number, preferably one from Similar_Part_Numbers, or "" if the \
description doesn't name a replacement (e.g. "NO LONGER AVAILABLE" with nothing else, or a note like "ONCE SOLD"). \
Never guess a part number that isn't written in the description or listed in Similar_Part_Numbers."""


def _ending_with(token: str, known: dict[str, str]) -> list[str]:
    # ponytail: linear scan of the item list per token - fine for the few dozen superseded lines.
    return [p for k, p in known.items() if k.endswith(token)] if len(token) >= 5 else []


def _similar(part: str, token: str, known: dict[str, str], n: int = 10) -> str:
    """Item-list candidates for Claude: items ending with the referenced token, then close spellings of it."""
    close = difflib.get_close_matches(token or part.upper(), known, n=n, cutoff=0.7)
    cands = _ending_with(token, known) + [known[k] for k in close]
    return " ".join([p for p in dict.fromkeys(cands) if p != part][:n])


def ai_resolve_replacements(lines: pd.DataFrame, known: dict[str, str]) -> pd.DataFrame:
    """Ask Claude only about lines the pattern couldn't resolve. No call if there are none."""
    todo = lines[lines["Found_By"] == ""]
    if todo.empty:
        return lines
    content = todo[["Part_Number", "Description"]].assign(
        Similar_Part_Numbers=[_similar(p, t, known) for p, t in zip(todo["Part_Number"], todo["Ref_Token"])]
    ).to_csv(index=False)
    result = _parse(require_client(), REPLACEMENT_PROMPT, content, Replacements)
    # Only accept answers that exist in the item list - anything else is treated as not found.
    answers = {r.part_number: known.get(r.replacement.strip().upper(), "") for r in result.lines}
    resolved = todo["Part_Number"].map(answers).fillna("")
    lines = lines.copy()
    lines.loc[todo.index, "Replacement"] = resolved
    lines.loc[todo.index, "Found_By"] = np.where(resolved != "", "Claude", "")
    return lines


def replacement_status(lines: pd.DataFrame, aged_df: pd.DataFrame, last_move: pd.Series,
                       as_at, review_m: float = 6) -> pd.DataFrame:
    """Is the replacement itself moving? That decides "sell old stock first" vs "write it off"."""
    months = ((pd.Timestamp(as_at) - lines["Replacement"].map(last_move)).dt.days / 30.4375).round(1)
    on_hand = aged_df.set_index("Part_Number")["Qty_On_Hand"]
    return lines.assign(
        Replacement_On_Hand=lines["Replacement"].map(on_hand).fillna(0),
        Replacement_Months_Since_Move=months,
        Replacement_Status=np.select(
            [lines["Replacement"] == "", months.isna(), months < review_m],
            ["No replacement found", "Replacement has no recorded movement",
             "Replacement is selling - sell old stock against its demand"],
            "Replacement is slow too - likely write off",
        ),
    )


def find_replacements(aged_df, reorder, tims, ns, as_at, tims_latest_month, review_m=6) -> pd.DataFrame:
    parts = reorder["Part_Number"].dropna().astype(str).str.strip()
    known = dict(zip(parts.str.upper(), parts))
    lines = ai_resolve_replacements(superseded_lines(aged_df, known), known)
    last_move = pd.concat([aged_stock.ns_last_sale(ns),
                           aged_stock.tims_last_move(tims, tims_latest_month)]).groupby(level=0).max()
    return replacement_status(lines, aged_df, last_move, as_at, review_m)
