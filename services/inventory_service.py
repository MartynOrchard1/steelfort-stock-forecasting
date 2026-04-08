import numpy as np
import pandas as pd
import streamlit as st

from services.file_loader import load_file_from_bytes
from utils.helpers import normalize_part_number


@st.cache_data(show_spinner=False)
def clean_inventory_data_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load and clean the inventory dataset into a standard structure.

    This gives the rest of the app predictable column names and numeric fields.
    """
    raw_df = load_file_from_bytes(file_bytes, file_name)
    df = raw_df.copy()

    df = df.dropna(how="all")
    df = df.loc[:, ~df.columns.astype(str).str.contains("^Unnamed", case=False, regex=True)]
    df.columns = [str(c).replace("\n", " ").strip() for c in df.columns]

    rename_map = {
        "POREF_PART": "Part_Number",
        "ITMAS_PART": "Part_Number",
        "PART": "Part_Number",
        "Part": "Part_Number",
        "Part #": "Part_Number",
        "part_number": "Part_Number",
        "ITMAS_NAME": "Description",
        "NAME": "Description",
        "Name": "Description",
        "Supplier": "POREF_SUPP",
        "SUPPLIER": "POREF_SUPP",
        "TYPE": "Type",
        "Part Type": "Type",
        "PART TYPE": "Type",
        "Qty On Hand": "Qty on hand",
        "Qty Alloc": "Qty Allocated",
        "Qty On Order": "Qty on Order",
        "Qty Avail": "Qty Available",
        "Location": "Loc",
        "Months on Hand": "Months on Hand",
        "Months  on Hand": "Months on Hand",
        "Months \non Hand": "Months on Hand",
    }
    df = df.rename(columns=rename_map)

    expected_defaults = {
        "Part_Number": "",
        "Description": "",
        "POREF_SUPP": "",
        "Type": "",
        "Qty on hand": 0,
        "Qty Allocated": 0,
        "Qty Available": 0,
        "Qty on Order": 0,
        "Min": 0,
        "Max": 0,
        "Loc": "",
        "6mAvg": 0,
        "6mUsage": 0,
        "12mAvg": 0,
        "12mUsage": 0,
        "EOQ": 0,
        "Months on Hand": np.nan,
        "Status": "",
    }

    for col, default_val in expected_defaults.items():
        if col not in df.columns:
            df[col] = default_val

    df["Part_Number"] = normalize_part_number(df["Part_Number"])
    df = df[(df["Part_Number"] != "") & (df["Part_Number"].str.lower() != "nan")]

    df["Description"] = df["Description"].astype(str).replace("nan", "")
    df["POREF_SUPP"] = df["POREF_SUPP"].astype("string").fillna("").str.strip()
    df["Type"] = df["Type"].astype("string").fillna("").str.strip()
    df["Loc"] = df["Loc"].astype(str).replace("nan", "")
    df["Status"] = df["Status"].astype(str).replace("nan", "")

    df["Is NLA?"] = df["Description"].str.upper().str.contains("NLA", na=False)

    numeric_cols = [
        "Qty on hand", "Qty Allocated", "Qty Available", "Qty on Order",
        "Min", "Max", "6mAvg", "6mUsage", "12mAvg", "12mUsage", "EOQ"
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["Months on Hand Numeric"] = pd.to_numeric(
        df["Months on Hand"].replace("#DIV/0!", pd.NA),
        errors="coerce"
    )

    df["Available Now"] = df["Qty on hand"] - df["Qty Allocated"]
    df["Net After POs"] = df["Qty on hand"] + df["Qty on Order"] - df["Qty Allocated"]

    df["Shortage to Min"] = (df["Min"] - df["Net After POs"]).clip(lower=0)
    df["Shortage to Max"] = (df["Max"] - df["Net After POs"]).clip(lower=0)
    df["Below Min?"] = df["Net After POs"] < df["Min"]

    df["Priority"] = "OK"
    df.loc[(df["Min"] > 0) & (df["Below Min?"]), "Priority"] = "Review"
    df.loc[(df["Min"] > 0) & (df["Below Min?"]) & (df["Qty Allocated"] > 0), "Priority"] = "High"
    df.loc[(df["Qty Allocated"] > 0) & (df["Net After POs"] <= 0), "Priority"] = "Urgent"

    return df


def apply_inventory_calculations(
    df: pd.DataFrame,
    demand_basis: str,
    months_target: int,
    use_eoq_rounding: bool,
    forecast_loaded: bool,
    custom_forecast_months: int,
    month_cols: list[str],
) -> pd.DataFrame:
    """
    Apply main ordering calculations after inventory and forecast data are merged.
    """
    df = df.copy()

    df["6mAvg"] = pd.to_numeric(df.get("6mAvg", 0), errors="coerce").fillna(0)
    df["12mAvg"] = pd.to_numeric(df.get("12mAvg", 0), errors="coerce").fillna(0)

    for col in month_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["Forecast Average"] = 0.0
    df["Forecast Months Used"] = 0

    if forecast_loaded and demand_basis == "Custom Forecast Average" and month_cols:
        selected_month_cols = month_cols[:custom_forecast_months]
        df["Forecast Average"] = df[selected_month_cols].mean(axis=1)
        df["Forecast Months Used"] = len(selected_month_cols)

        df["Demand_Per_Month_Used"] = np.where(
            df["Forecast Average"].fillna(0) > 0,
            df["Forecast Average"],
            np.where(df["6mAvg"].fillna(0) > 0, df["6mAvg"], df["12mAvg"].fillna(0)),
        )

    elif forecast_loaded and demand_basis == "Forecast_Weighted_6m":
        df["Forecast_Weighted_6m"] = pd.to_numeric(
            df.get("Forecast_Weighted_6m", 0), errors="coerce"
        ).fillna(0)
        df["Forecast Average"] = df["Forecast_Weighted_6m"]
        df["Forecast Months Used"] = 6

        df["Demand_Per_Month_Used"] = np.where(
            df["Forecast_Weighted_6m"].fillna(0) > 0,
            df["Forecast_Weighted_6m"],
            np.where(df["6mAvg"].fillna(0) > 0, df["6mAvg"], df["12mAvg"].fillna(0)),
        )

    else:
        if demand_basis not in df.columns:
            df[demand_basis] = 0

        df[demand_basis] = pd.to_numeric(df[demand_basis], errors="coerce").fillna(0)
        df["Demand_Per_Month_Used"] = df[demand_basis]
        df["Forecast Average"] = df[demand_basis]

        if demand_basis == "6mAvg":
            df["Forecast Months Used"] = 6
        elif demand_basis == "12mAvg":
            df["Forecast Months Used"] = 12

    df["Available"] = df["Qty on hand"] - df["Qty Allocated"]
    df["Target Stock"] = df["Demand_Per_Month_Used"] * months_target
    df["Recommended Order"] = np.ceil(df["Target Stock"] - df["Available"]).clip(lower=0)

    if use_eoq_rounding:
        valid_eoq = df["EOQ"] > 1
        df["Recommended Order"] = np.where(
            valid_eoq & (df["Recommended Order"] > 0),
            np.ceil(df["Recommended Order"] / df["EOQ"]) * df["EOQ"],
            df["Recommended Order"],
        )

    df["Recommended Order"] = pd.to_numeric(df["Recommended Order"], errors="coerce").fillna(0).astype(int)

    df["Effective Min"] = np.where(
        pd.to_numeric(df["Min"], errors="coerce").fillna(0) > 0,
        pd.to_numeric(df["Min"], errors="coerce").fillna(0),
        5,
    )

    df["Priority V2"] = "🟢 OK"
    df.loc[df["Net After POs"] < 0, "Priority V2"] = "🔴 URGENT"
    df.loc[
        (df["Net After POs"] >= 0) & (df["Net After POs"] < df["Effective Min"]),
        "Priority V2"
    ] = "🟡 REPLENISH"

    return df


def apply_inventory_filters(
    df: pd.DataFrame,
    main_filter_col: str,
    selected_main_filters: list,
    selected_locations: list,
    selected_priorities: list,
    only_below_min: bool,
    only_allocated: bool,
    exclude_nla: bool,
    only_need_order: bool,
    text_search: str,
    hide_poc: bool,
    hide_poxpb: bool,
    hide_pox: bool,
    hide_ref_descriptions: bool,
    show_poxpb: bool,
    show_poc_only: bool,
    show_pox_only: bool,
    show_poxcc_only: bool,
) -> pd.DataFrame:
    """
    Apply all table filters in one place.
    """
    filtered = df.copy()

    if selected_main_filters:
        filtered = filtered[filtered[main_filter_col].isin(selected_main_filters)]

    if selected_locations:
        filtered = filtered[filtered["Loc"].astype(str).str.strip().isin(selected_locations)]

    if selected_priorities:
        filtered = filtered[filtered["Priority V2"].isin(selected_priorities)]

    if only_below_min:
        filtered = filtered[filtered["Below Min?"]]

    if only_allocated:
        filtered = filtered[filtered["Qty Allocated"] > 0]

    if exclude_nla:
        filtered = filtered[~filtered["Is NLA?"]]

    if only_need_order:
        filtered = filtered[filtered["Recommended Order"] > 0]

    if text_search:
        q = text_search.strip().lower()
        filtered = filtered[
            filtered["Part_Number"].astype(str).str.lower().str.contains(q, na=False) |
            filtered["Description"].astype(str).str.lower().str.contains(q, na=False)
        ]

    part_series = filtered["Part_Number"].astype(str).str.upper()

    if hide_poc:
        filtered = filtered[~part_series.str.startswith("POC")]

    if hide_poxpb:
        filtered = filtered[~part_series.str.startswith("POXPB")]

    if hide_pox:
        filtered = filtered[~part_series.str.startswith("POX")]

    if hide_ref_descriptions:
        desc_series = filtered["Description"].astype(str).str.upper()
        filtered = filtered[
            ~(
                desc_series.str.contains("REF", na=False) |
                desc_series.str.contains("OBS", na=False)
            )
        ]

    part_series = filtered["Part_Number"].astype(str).str.upper()

    if show_poxpb:
        filtered = filtered[part_series.str.startswith("POXPB")]
    elif show_poc_only:
        filtered = filtered[part_series.str.startswith("POC")]
    elif show_pox_only:
        filtered = filtered[part_series.str.startswith("POX")]

    if show_poxcc_only:
        filtered = filtered[part_series.str.startswith("POXCC")]

    return filtered