import csv
import io

import pandas as pd
import streamlit as st

from utils.helpers import normalize_part_number

# NetSuite's "Custom Inventory Back Order Report" groups rows first by item
# type, then by item, with individual open Sales Order lines beneath each
# item and a "Total - <item>" subtotal row carrying the true Back Ordered
# figure. This is a grouped/hierarchical export, not a flat table, so it
# needs its own parser rather than reusing file_loader's flat CSV logic.
TYPE_MARKERS = {"Assembly", "Inventory Item", "Non-inventory Item", "Kit/Package", "Other Charge", "Group"}


@st.cache_data(show_spinner=False)
def load_backorder_report_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Parse the NetSuite Custom Inventory Back Order Report export into one
    row per item: Part_Number, Back Ordered qty, number of distinct
    customers waiting, and the oldest outstanding backorder date.
    """
    text = file_bytes.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(io.StringIO(text)))

    header_idx = None
    for i, row in enumerate(rows):
        cleaned = [str(c).strip() for c in row]
        if "Item" in cleaned and "Back Ordered" in cleaned:
            header_idx = i
            break

    if header_idx is None:
        raise ValueError(
            "Could not find a valid header row in the Back Order report "
            "(expected columns like 'Item' and 'Back Ordered')."
        )

    header = [str(c).strip() for c in rows[header_idx]]
    col_index = {name: idx for idx, name in enumerate(header)}

    def get(row, name, default=""):
        idx = col_index.get(name)
        if idx is None or idx >= len(row):
            return default
        return row[idx]

    records = []
    current_dates = []
    current_customers = set()

    for row in rows[header_idx + 1:]:
        if not any(str(c).strip() for c in row):
            continue

        first = str(row[0]).strip()

        if first in TYPE_MARKERS:
            continue

        if first.startswith("Total - "):
            item_name = first[len("Total - "):].strip()

            if item_name in TYPE_MARKERS:
                # Subtotal for a whole type group (e.g. "Total - Inventory
                # Item"), not an individual item - skip it.
                current_dates = []
                current_customers = set()
                continue

            back_ordered_raw = str(get(row, "Back Ordered", "0")).replace(",", "").strip()
            try:
                back_ordered = float(back_ordered_raw) if back_ordered_raw else 0.0
            except ValueError:
                back_ordered = 0.0

            oldest_date = min(current_dates) if current_dates else pd.NaT

            records.append({
                "Part_Number": item_name,
                "Back Ordered": back_ordered,
                "Backorder Customers": len(current_customers),
                "Oldest Backorder Date": oldest_date,
            })

            current_dates = []
            current_customers = set()
            continue

        if first == "Total":
            continue

        date_val = str(get(row, "Date")).strip()

        if first and not date_val:
            # New item group header row - just marks the start, the real
            # data comes from the transaction rows and Total row below it.
            current_dates = []
            current_customers = set()
            continue

        # Transaction detail row belonging to the current item group.
        if date_val:
            parsed_date = pd.to_datetime(date_val, dayfirst=True, errors="coerce")
            if pd.notna(parsed_date):
                current_dates.append(parsed_date)

        customer = str(get(row, "Customer")).strip()
        if customer:
            current_customers.add(customer)

    columns = ["Part_Number", "Back Ordered", "Backorder Customers", "Oldest Backorder Date"]

    if not records:
        return pd.DataFrame(columns=columns)

    df = pd.DataFrame(records, columns=columns)
    df["Part_Number"] = normalize_part_number(df["Part_Number"])
    df["Back Ordered"] = pd.to_numeric(df["Back Ordered"], errors="coerce").fillna(0)
    df["Backorder Customers"] = pd.to_numeric(df["Backorder Customers"], errors="coerce").fillna(0).astype(int)

    # A part can appear more than once if NetSuite split it across type
    # groups - combine down to one row per part.
    df = df.groupby("Part_Number", as_index=False).agg({
        "Back Ordered": "sum",
        "Backorder Customers": "sum",
        "Oldest Backorder Date": "min",
    })

    return df
