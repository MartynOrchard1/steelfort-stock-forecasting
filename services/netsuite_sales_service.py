import csv
import io

import pandas as pd
import streamlit as st

from utils.helpers import normalize_part_number

# 02 = Credits/Recharges/stock loss - moving stock here isn't a real sale,
# so it's excluded from replenishment demand. CS (Consignment Stock) is
# deliberately NOT excluded - it's stock waiting to sell, treated the same
# as a normal sale for demand purposes.
EXCLUDED_LOCATION_CODES = {"02"}

# A saved search meant to cover the trailing 24 months should span close to
# 730 days of transactions. If it spans a lot less than that (e.g. the
# search's Date filter got reset to something like "This month"), the
# figures below are not a 24-month picture and shouldn't be trusted as one.
MIN_EXPECTED_SALES_HISTORY_DAYS = 300

REQUIRED_COLUMNS = ["Location", "Part Number", "Sum of Quantity", "Sum of Amount"]

DETAIL_COLUMNS = [
    "Part_Number", "Loc", "NetSuite Qty Sold", "NetSuite Revenue Sold",
    "NetSuite Last Sale Date", "NetSuite First Sale Date",
]


@st.cache_data(show_spinner=False)
def load_netsuite_sales_history_cached(file_bytes: bytes, file_name: str) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """
    Parse the NetSuite "Total Sales by Item/Location" saved search export.

    This is a grouped/summarized saved search result, not a flat table, so
    it needs its own parser rather than reusing file_loader's generic
    header-scanning logic (same reasoning as backorder_service.py).

    NetSuite's on-screen "Export - CSV" always appends an "Overall Total"
    row for a grouped/summarized search (blank Part Number, comma-formatted
    Sum of Quantity, e.g. "10,062.0"). That row is dropped by content
    (blank Part Number / Location == "Overall Total"), not by row position,
    so this keeps working even if a future export sorts rows differently.

    Returns (detail, grouped, date_span_days):
    - detail: one row per Part_Number/Location as exported, before the
      location exclusion below is applied.
    - grouped: one row per Part_Number, summed across kept locations
      (Location "02" - Credits/Recharges/stock loss - excluded).
    - date_span_days: days between the earliest First Sale Date and the
      latest Last Sale Date across the whole file. The caller uses this to
      warn when the saved search isn't actually covering ~24 months yet.
    """
    text = file_bytes.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))

    if not rows:
        raise ValueError("NetSuite sales history file is empty.")

    header = [str(c).strip() for c in rows[0]]
    col_index = {name: idx for idx, name in enumerate(header)}

    missing = [c for c in REQUIRED_COLUMNS if c not in col_index]
    if missing:
        raise ValueError(f"NetSuite sales history file is missing expected column(s): {missing}")

    def get(row, name, default=""):
        idx = col_index.get(name)
        if idx is None or idx >= len(row):
            return default
        return row[idx]

    records = []
    for row in rows[1:]:
        if not any(str(c).strip() for c in row):
            continue

        part_number = str(get(row, "Part Number")).strip()
        location_raw = str(get(row, "Location")).strip()

        # NetSuite's grand-total row for the whole grouped search.
        if not part_number or location_raw == "Overall Total":
            continue

        qty_raw = str(get(row, "Sum of Quantity", "0")).replace(",", "").strip()
        amount_raw = str(get(row, "Sum of Amount", "0")).replace(",", "").strip()

        try:
            qty = float(qty_raw) if qty_raw else 0.0
        except ValueError:
            qty = 0.0

        try:
            amount = float(amount_raw) if amount_raw else 0.0
        except ValueError:
            amount = 0.0

        # NetSuite location is "CODE - Description" (e.g. "SS - PALM NTH
        # FACTORY SS"); keep just the code so it matches "Loc" elsewhere.
        location_code = location_raw.split(" - ", 1)[0].strip()

        records.append({
            "Part_Number": part_number,
            "Loc": location_code,
            "NetSuite Qty Sold": qty,
            "NetSuite Revenue Sold": amount,
            "NetSuite Last Sale Date": str(get(row, "Last Sale Date")).strip(),
            "NetSuite First Sale Date": str(get(row, "First ever Sale Date")).strip(),
        })

    if not records:
        empty = pd.DataFrame(columns=DETAIL_COLUMNS)
        return empty, empty, 0

    detail = pd.DataFrame(records, columns=DETAIL_COLUMNS)
    detail["Part_Number"] = normalize_part_number(detail["Part_Number"])

    detail["NetSuite Last Sale Date"] = pd.to_datetime(
        detail["NetSuite Last Sale Date"], dayfirst=True, errors="coerce"
    )
    detail["NetSuite First Sale Date"] = pd.to_datetime(
        detail["NetSuite First Sale Date"], dayfirst=True, errors="coerce"
    )

    date_span_days = 0
    has_last = detail["NetSuite Last Sale Date"].notna().any()
    has_first = detail["NetSuite First Sale Date"].notna().any()
    if has_last and has_first:
        span = detail["NetSuite Last Sale Date"].max() - detail["NetSuite First Sale Date"].min()
        date_span_days = int(span.days)

    kept = detail[~detail["Loc"].isin(EXCLUDED_LOCATION_CODES)].copy()

    grouped = kept.groupby("Part_Number", as_index=False).agg(**{
        "NetSuite Qty Sold": ("NetSuite Qty Sold", "sum"),
        "NetSuite Revenue Sold": ("NetSuite Revenue Sold", "sum"),
        "NetSuite Last Sale Date": ("NetSuite Last Sale Date", "max"),
        "NetSuite First Sale Date": ("NetSuite First Sale Date", "min"),
    })

    return detail, grouped, date_span_days
