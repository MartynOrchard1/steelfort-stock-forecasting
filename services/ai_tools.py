"""
Tools the chat model can call over the FULL loaded dataframe, plus tolerant part-number matching.
Every tool returns only a handful of relevant columns/rows so token use stays small.
"""

import re
from datetime import date, timedelta

import pandas as pd

MAX_ROWS = 25
SAFETY_DAYS_DEFAULT = 7
DETAIL_COLS = [
    "Part_Number", "Description", "POREF_SUPP", "Spring Category", "Part Group",
    "Qty on hand", "Qty Allocated", "Qty on Order", "Back Ordered",
    "Demand_Per_Month_Used", "Recommended Order", "Priority V2",
]
HAYSTACK_COLS = ["Part_Number", "Description", "POREF_SUPP", "Spring Category", "Part Group", "Part Type"]
LEAD_COL = "Lead Time Days"


def norm_sku(value) -> str:
    """Upper-case, drop everything but letters/digits, drop a leading PV so PVHA27390A == HA27390A."""
    s = re.sub(r"[^A-Z0-9]", "", str(value).upper())
    return s[2:] if s.startswith("PV") and len(s) > 2 else s


def extract_sku_tokens(text: str) -> list[str]:
    """SKU-like tokens (letters+digits mixed, 5+ chars) and 12-14 digit barcodes, de-duplicated in order."""
    out = []
    for tok in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-/]{3,}", text):
        has_d, has_a = any(c.isdigit() for c in tok), any(c.isalpha() for c in tok)
        if (has_d and has_a and len(tok) >= 5) or (tok.isdigit() and 12 <= len(tok) <= 14):
            if tok.upper() not in (t.upper() for t in out):
                out.append(tok)
    return out


def _barcode_cols(df):
    return [c for c in df.columns if re.search(r"barcode|upc|ean|gtin", str(c), re.I)]


def _cols(df, cols):
    return [c for c in cols if c in df.columns]


def _match_skus(df, skus):
    """Returns (matched rows, {sku: how matched or None})."""
    keys = df["Part_Number"].map(norm_sku)
    bcols = _barcode_cols(df)
    hits, report = [], {}
    for sku in skus:
        k = norm_sku(sku)
        mask, how = keys == k, "part number"
        if not mask.any():
            for b in bcols:
                mask = df[b].astype(str).str.replace(r"\.0$", "", regex=True).str.strip() == str(sku).strip()
                if mask.any():
                    how = f"barcode ({b})"
                    break
        if not mask.any():
            mask = df["Description"].astype(str).str.upper().str.contains(re.escape(str(sku).upper()), na=False)
            how = "description"
        report[sku] = how if mask.any() else None
        if mask.any():
            hits.append(df[mask])
    rows = pd.concat(hits).drop_duplicates("Part_Number") if hits else df.iloc[0:0]
    return rows, report


def _select(df, skus=None, text=None, supplier=None, category=None):
    report = None
    if skus:
        df, report = _match_skus(df, skus)
    if text:
        hay = df[_cols(df, HAYSTACK_COLS)].astype(str).agg(" ".join, axis=1).str.upper()
        for w in text.upper().split():
            df = df[hay.loc[df.index].str.contains(re.escape(w), na=False)]
    if supplier:
        df = df[df["POREF_SUPP"].astype(str).str.upper().str.contains(re.escape(supplier.upper()), na=False)]
    if category:
        cat = pd.Series("", index=df.index)
        for c in ("Spring Category", "Part Group", "Part Type"):
            if c in df.columns:
                cat = cat + " " + df[c].astype(str)
        df = df[cat.str.upper().str.contains(re.escape(category.upper()), na=False)]
    return df, report


def _records(df):
    return df[_cols(df, DETAIL_COLS + _barcode_cols(df))].head(MAX_ROWS).round(2).to_dict("records")


def find_parts(df, skus=None, text=None, supplier=None, category=None, **_):
    rows, report = _select(df, skus, text, supplier, category)
    out = {"matched_count": len(rows), "shown": min(len(rows), MAX_ROWS), "parts": _records(rows)}
    if report is not None:
        out["sku_match"] = {s: (h or "NOT FOUND") for s, h in report.items()}
    return out


def get_part_detail(df, sku, **_):
    rows, _r = _select(df, [sku])
    if rows.empty:
        return {"error": f"{sku} not found"}
    extra = ["Min", "Max", "EOQ", "6mAvg", "12mAvg", "Net After POs", "Order Reason", "NetSuite Last Sale Date"]
    r = rows.iloc[0]
    d = {c: r[c] for c in _cols(rows, DETAIL_COLS + extra)}
    d.update(order_timing(df, [sku], **_)["parts"][0])
    return {k: (None if pd.isna(v) else v) if not isinstance(v, (list, dict)) else v for k, v in d.items()}


def order_timing(df, skus, lead_days=0, safety_days=SAFETY_DAYS_DEFAULT, fast_threshold=None, **_):
    rows, report = _select(df, skus)
    lead_missing = LEAD_COL not in rows.columns and not lead_days
    if "Demand_Per_Month_Used" not in df.columns:
        return {"error": "Column 'Demand_Per_Month_Used' (sales velocity) is missing from the data"}
    if fast_threshold is None:
        pos = df["Demand_Per_Month_Used"][df["Demand_Per_Month_Used"] > 0]
        fast_threshold = float(pos.quantile(0.9)) if len(pos) else 0
    today, parts = date.today(), []
    for _i, r in rows.head(MAX_ROWS).iterrows():
        supply = r["Qty on hand"] - r["Qty Allocated"] + r["Qty on Order"]
        daily = r["Demand_Per_Month_Used"] * 12 / 365
        lead = r[LEAD_COL] if LEAD_COL in rows.columns and pd.notna(r[LEAD_COL]) else lead_days
        p = {"Part_Number": r["Part_Number"], "Description": r["Description"],
             "available_incl_on_order": round(float(supply), 1), "avg_daily_sales": round(float(daily), 2),
             "recommended_order_qty": int(r.get("Recommended Order", 0)),
             "fast_seller": bool(r["Demand_Per_Month_Used"] >= fast_threshold > 0)}
        if daily <= 0:
            p["note"] = "no sales velocity (Demand_Per_Month_Used is 0) - cover/stock-out date can't be calculated"
        else:
            cover = supply / daily
            p["days_of_cover"] = round(float(cover), 1)
            if cover <= 0:
                p["stock_out_date"] = "already out / short"
            else:
                p["stock_out_date"] = (today + timedelta(days=cover)).isoformat()
            if lead:
                p["latest_order_date"] = (today + timedelta(days=cover - lead - safety_days)).isoformat()
                if cover - lead - safety_days < 0:
                    p["latest_order_date"] += " (OVERDUE - order now)"
        parts.append(p)
    out = {"today": today.isoformat(), "lead_time_days": lead_days or None, "safety_buffer_days": safety_days,
           "fast_seller_threshold_units_per_month": round(fast_threshold, 1), "parts": parts}
    if lead_missing:
        out["warning"] = (f"No lead time available: data has no '{LEAD_COL}' column and none was set in the chat "
                          "panel, so latest_order_date can't be calculated. Say this to the user.")
    if report:
        out["not_found"] = [s for s, h in report.items() if not h]
    return out


def summarise(df, skus=None, text=None, supplier=None, category=None, **_):
    rows, _r = _select(df, skus, text, supplier, category)
    if rows.empty:
        return {"parts": 0}
    out = {"parts": len(rows)}
    for c in ("Qty on hand", "Qty Allocated", "Qty on Order", "Back Ordered", "Recommended Order"):
        if c in rows.columns:
            out[f"total {c}"] = int(rows[c].sum())
    if "Priority V2" in rows.columns:
        out["priority_counts"] = rows["Priority V2"].value_counts().to_dict()
    if "Recommended Order" in rows.columns:
        out["top_by_recommended_order"] = _records(rows.sort_values("Recommended Order", ascending=False).head(10))
    return out


_FILTERS = {
    "skus": {"type": "array", "items": {"type": "string"}, "description": "Part numbers/barcodes (any format, PV prefix optional)"},
    "text": {"type": "string", "description": "Words that must all appear in part number/description/supplier/category"},
    "supplier": {"type": "string", "description": "Supplier code or name fragment"},
    "category": {"type": "string", "description": "Category / part group / part type fragment"},
}
TOOLS = [
    {"name": "find_parts", "description": "Search the full dataset. Returns matching rows and, for skus, which matched/not found.",
     "input_schema": {"type": "object", "properties": _FILTERS}},
    {"name": "get_part_detail", "description": "Full detail for one part incl. stock, velocity, recommended order, timing.",
     "input_schema": {"type": "object", "properties": {"sku": {"type": "string"}}, "required": ["sku"]}},
    {"name": "order_timing", "description": "Per part: days of cover, stock-out date, latest order date (lead time + safety), suggested qty, fast-seller flag.",
     "input_schema": {"type": "object", "properties": {"skus": _FILTERS["skus"]}, "required": ["skus"]}},
    {"name": "summarise", "description": "Totals and priority counts for a set of parts (same filters as find_parts).",
     "input_schema": {"type": "object", "properties": _FILTERS}},
]
_FUNCS = {"find_parts": find_parts, "get_part_detail": get_part_detail, "order_timing": order_timing, "summarise": summarise}


def run_tool(name, args, df, lead_days=0, safety_days=SAFETY_DAYS_DEFAULT):
    try:
        extra = {"lead_days": lead_days, "safety_days": safety_days} if name in ("order_timing", "get_part_detail") else {}
        return _FUNCS[name](df, **args, **extra)
    except Exception as e:  # surface to the model so it can retry/report
        return {"error": f"{type(e).__name__}: {e}"}
