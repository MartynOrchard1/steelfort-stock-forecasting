import csv
import io
import re

import numpy as np
import pandas as pd
import streamlit as st

# =========================================================
# CONFIG / CONSTANTS
# =========================================================

APP_TITLE = "Steelfort Stock Forecasting"
APP_CAPTION = "Version 8.0.0 - Unified single-file Streamlit app"

WORKSHEET_TYPES = ["Power Parts", "All Parts", "MTD", "Bunnings"]

HEADER_MARKERS = {
    "POREF_PART",
    "Part_Number",
    "ITMAS_PART",
    "Part",
    "PART",
    "Type",
    "TYPE",
    "ith_part",
    "part_number",
    "Part #",
    "Steelfort Sku",
    "SKU",
    "Bunnings Item Number",
    "Item Description",
}

MODE_DETAILS = {
    "Power Parts": {
        "subtitle": "Review standard Power Parts inventory with optional forecast enrichment.",
        "required": "Inventory file required. Forecast file optional.",
        "focus": [
            "Supplier-led ordering review",
            "Demand basis selection",
            "Order-focused filtering and part drilldown",
        ],
    },
    "All Parts": {
        "subtitle": "Work across the wider inventory dataset with the same ordering workflow.",
        "required": "Inventory file required. Forecast file optional.",
        "focus": [
            "Full inventory coverage",
            "Same filtering and ordering workflow",
            "Useful when you do not want a narrower worksheet subset",
        ],
    },
    "MTD": {
        "subtitle": "Review MTD-style inventory with type-based filtering and default location support.",
        "required": "Inventory file required. Forecast file optional.",
        "focus": [
            "Type-led filtering",
            "MTD review workflow",
            "Compatible with forecast upload and recommendation logic",
        ],
    },
    "Bunnings": {
        "subtitle": "Dedicated Bunnings weeks-on-hand workflow using the Bunnings sheet plus raw forecast history.",
        "required": "Both the Bunnings file and forecast dataset are required.",
        "focus": [
            "Weeks on hand estimation",
            "Forecast by location mapping for VU / PV parts",
            "Exportable Bunnings review output",
        ],
    },
}

SESSION_KEYS_TO_CLEAR = [
    "selected_worksheet_type",
    "inventory_file",
    "forecast_file",
    "bunnings_file_direct",
    "forecast_file_bunnings_direct",
    "order_review_table",
    "bunnings_search_direct",
]

# =========================================================
# GENERAL HELPERS
# =========================================================


def safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def get_filter_config(worksheet_type: str) -> tuple[str, str]:
    if worksheet_type == "MTD":
        return "Type", "Type"
    return "POREF_SUPP", "Supplier"


def get_uploaded_file_bytes(uploaded_file):
    if uploaded_file is None:
        return None, None
    return uploaded_file.getvalue(), uploaded_file.name


def normalize_part_number(series: pd.Series) -> pd.Series:
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .str.replace(r"\.0$", "", regex=True)
        .str.upper()
    )


def format_numeric_display(value):
    if pd.isna(value):
        return ""
    if isinstance(value, (int, np.integer)):
        return f"{int(value):,}"
    if isinstance(value, (float, np.floating)):
        if float(value).is_integer():
            return f"{int(value):,}"
        return f"{value:,.2f}"
    return str(value)


def get_forecast_month_columns_newest_first(columns) -> list[str]:
    """
    IMPORTANT:
    Forecast headers are reversed in the export:
    - ith_24 = most recent month
    - ith_01 = oldest month
    """
    month_cols = [c for c in columns if re.fullmatch(r"ith_\d{2}", str(c))]
    return sorted(month_cols, key=lambda x: int(str(x).split("_")[1]), reverse=True)


def _safe_file_name(session_key: str) -> str:
    uploaded = st.session_state.get(session_key)
    if uploaded is None:
        return "Not loaded"
    return getattr(uploaded, "name", "Loaded")


def _clear_app_state() -> None:
    for key in SESSION_KEYS_TO_CLEAR:
        if key in st.session_state:
            del st.session_state[key]


# =========================================================
# FILE LOADING
# =========================================================


@st.cache_data(show_spinner=False)
def load_file_from_bytes(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load a CSV or Excel file from raw bytes.

    The loader scans for a valid header row so the app can handle
    messy exports where headers are not always on row 1.
    """
    file_name = file_name.lower()

    if file_name.endswith(".csv"):
        text = file_bytes.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))

        header_index = None
        for i, row in enumerate(rows):
            cleaned = [str(cell).replace("\n", " ").strip() for cell in row]
            if any(cell in HEADER_MARKERS for cell in cleaned):
                header_index = i
                break

        if header_index is None:
            raise ValueError("Could not find a valid header row in the CSV.")

        header = [str(x).replace("\n", " ").strip() for x in rows[header_index]]
        data_rows = rows[header_index + 1 :]

        fixed_rows = []
        header_len = len(header)

        for row in data_rows:
            if not row or all(str(cell).strip() == "" for cell in row):
                continue

            if len(row) < header_len:
                row = row + [""] * (header_len - len(row))
            elif len(row) > header_len:
                row = row[:header_len]

            fixed_rows.append(row)

        return pd.DataFrame(fixed_rows, columns=header)

    excel_buffer = io.BytesIO(file_bytes)
    df = pd.read_excel(excel_buffer, header=None)

    header_index = None
    max_scan = min(30, len(df))

    for i in range(max_scan):
        row_values = [str(x).replace("\n", " ").strip() for x in df.iloc[i].tolist()]
        if any(val in HEADER_MARKERS for val in row_values):
            header_index = i
            break

    if header_index is None:
        raise ValueError("Could not find a valid header row in the Excel file.")

    header = [str(x).replace("\n", " ").strip() for x in df.iloc[header_index].tolist()]
    df = df.iloc[header_index + 1 :].copy()
    df.columns = header
    df = df.reset_index(drop=True)

    return df


# =========================================================
# FORECAST SERVICES
# =========================================================


@st.cache_data(show_spinner=False)
def load_forecast_history_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load and aggregate forecasting history by part number.

    IMPORTANT:
    ith_24 is the most recent month.
    """
    raw = load_file_from_bytes(file_bytes, file_name).copy()
    raw.columns = [str(c).replace("\n", " ").strip().lower() for c in raw.columns]

    rename_map = {
        "part_number": "ith_part",
        "part": "ith_part",
        "loc": "ith_loc",
        "location": "ith_loc",
    }
    raw = raw.rename(columns=rename_map)

    if "ith_part" not in raw.columns:
        raise ValueError("Forecasting file must contain 'ith_part' or equivalent part number column.")

    if "ith_loc" not in raw.columns:
        raw["ith_loc"] = ""

    month_cols = [c for c in raw.columns if re.fullmatch(r"ith_\d{2}", c)]
    if not month_cols:
        raise ValueError("Forecasting file must contain monthly columns like ith_01 to ith_24.")

    keep_cols = ["ith_part", "ith_loc"] + month_cols
    raw = raw[keep_cols].copy()

    raw["ith_part"] = normalize_part_number(raw["ith_part"])
    raw = raw[(raw["ith_part"] != "") & (raw["ith_part"].str.lower() != "nan")]

    for col in month_cols:
        raw[col] = pd.to_numeric(raw[col], errors="coerce").fillna(0)

    grouped = raw.groupby("ith_part", as_index=False)[month_cols].sum()

    newest_first = get_forecast_month_columns_newest_first(month_cols)
    newest_3 = newest_first[:3]
    newest_6 = newest_first[:6]
    newest_12 = newest_first[:12]

    grouped["Forecast_3m_Total"] = grouped[newest_3].sum(axis=1)
    grouped["Forecast_6m_Total"] = grouped[newest_6].sum(axis=1)
    grouped["Forecast_12m_Total"] = grouped[newest_12].sum(axis=1)

    grouped["Forecast_3m_Avg"] = grouped[newest_3].mean(axis=1)
    grouped["Forecast_6m_Avg"] = grouped[newest_6].mean(axis=1)
    grouped["Forecast_12m_Avg"] = grouped[newest_12].mean(axis=1)

    weights = np.array([6, 5, 4, 3, 2, 1], dtype=float)
    weighted_values = grouped[newest_6].to_numpy(dtype=float)
    grouped["Forecast_Weighted_6m"] = (weighted_values * weights).sum(axis=1) / weights.sum()

    grouped = grouped.rename(columns={"ith_part": "Part_Number"})
    grouped["Part_Number"] = normalize_part_number(grouped["Part_Number"])

    return grouped


@st.cache_data(show_spinner=False)
def merge_inventory_and_forecast(inventory_df: pd.DataFrame, forecast_df: pd.DataFrame | None) -> pd.DataFrame:
    if forecast_df is None:
        return inventory_df.copy()
    return inventory_df.merge(forecast_df, on="Part_Number", how="left")


# =========================================================
# INVENTORY SERVICES
# =========================================================


@st.cache_data(show_spinner=False)
def clean_inventory_data_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load and clean the inventory dataset into a standard structure.
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
        "Qty on hand",
        "Qty Allocated",
        "Qty Available",
        "Qty on Order",
        "Min",
        "Max",
        "6mAvg",
        "6mUsage",
        "12mAvg",
        "12mUsage",
        "EOQ",
    ]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["Months on Hand Numeric"] = pd.to_numeric(
        df["Months on Hand"].replace("#DIV/0!", pd.NA),
        errors="coerce",
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

    df["Base Recommended Order"] = np.ceil(df["Target Stock"] - df["Net After POs"]).clip(lower=0)

    if use_eoq_rounding:
        valid_eoq = df["EOQ"] > 1
        df["Base Recommended Order"] = np.where(
            valid_eoq & (df["Base Recommended Order"] > 0),
            np.ceil(df["Base Recommended Order"] / df["EOQ"]) * df["EOQ"],
            df["Base Recommended Order"],
        )

    df["Base Recommended Order"] = (
        pd.to_numeric(df["Base Recommended Order"], errors="coerce").fillna(0).astype(int)
    )

    df["Recommended Order"] = df["Base Recommended Order"]

    df["Effective Min"] = np.where(
        pd.to_numeric(df["Min"], errors="coerce").fillna(0) > 0,
        pd.to_numeric(df["Min"], errors="coerce").fillna(0),
        5,
    )

    df["Priority V2"] = "🟢 OK"
    df.loc[df["Net After POs"] < 0, "Priority V2"] = "🔴 URGENT"
    df.loc[
        (df["Net After POs"] >= 0) & (df["Net After POs"] < df["Effective Min"]),
        "Priority V2",
    ] = "🟡 REPLENISH"

    df["Recommended Order"] = (
        pd.to_numeric(df["Recommended Order"], errors="coerce").fillna(0).astype(int)
    )

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
            filtered["Part_Number"].astype(str).str.lower().str.contains(q, na=False)
            | filtered["Description"].astype(str).str.lower().str.contains(q, na=False)
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
                desc_series.str.contains("REF", na=False)
                | desc_series.str.contains("OBS", na=False)
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


# =========================================================
# BUNNINGS SERVICES
# =========================================================


@st.cache_data(show_spinner=False)
def clean_bunnings_file_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    raw_df = load_file_from_bytes(file_bytes, file_name).copy()
    raw_df = raw_df.dropna(how="all")
    raw_df = raw_df.loc[:, ~raw_df.columns.astype(str).str.contains("^Unnamed", case=False, regex=True)]
    raw_df.columns = [str(c).replace("\n", " ").strip() for c in raw_df.columns]

    rename_map = {
        "Steelfort Sku": "Steelfort_Sku",
        "Steelfort SKU": "Steelfort_Sku",
        "Steelfort Sku ": "Steelfort_Sku",
        "SKU": "SKU",
        "Item Description": "Item_Description",
        "Status": "Status",
        "CY24 Sales": "CY24_Sales",
        "SOH Steelfort": "SOH_Steelfort",
        "SOH Steelfort 7 April 26": "SOH_Steelfort",
        "SOH Steelfort 7 April 2026": "SOH_Steelfort",
        "Ex China QTY": "Ex_China_QTY",
        "China Orders ETA": "China_Orders_ETA",
        "Purchase orders": "Purchase_Orders",
        "Bunnings Item Number": "Bunnings_Item_Number",
    }

    cleaned_columns = {}
    for c in raw_df.columns:
        clean_c = str(c).strip()
        cleaned_columns[c] = rename_map.get(clean_c, clean_c)
    raw_df = raw_df.rename(columns=cleaned_columns)

    required_defaults = {
        "Steelfort_Sku": "",
        "SKU": "",
        "Item_Description": "",
        "Status": "",
        "CY24_Sales": 0,
        "SOH_Steelfort": 0,
        "Ex_China_QTY": 0,
        "China_Orders_ETA": "",
        "Purchase_Orders": 0,
        "Bunnings_Item_Number": "",
    }

    for col, default_val in required_defaults.items():
        if col not in raw_df.columns:
            raw_df[col] = default_val

    raw_df["Steelfort_Sku"] = normalize_part_number(raw_df["Steelfort_Sku"])
    raw_df["SKU"] = normalize_part_number(raw_df["SKU"])

    raw_df["Match_Part"] = np.where(
        raw_df["Steelfort_Sku"].astype(str).str.strip() != "",
        raw_df["Steelfort_Sku"],
        raw_df["SKU"],
    )
    raw_df["Match_Part"] = normalize_part_number(pd.Series(raw_df["Match_Part"]))

    raw_df = raw_df[
        (raw_df["Match_Part"] != "") & (raw_df["Match_Part"].str.lower() != "nan")
    ].copy()

    raw_df["Forecast_Loc"] = ""
    raw_df.loc[raw_df["Match_Part"].str.startswith("VU", na=False), "Forecast_Loc"] = "DC"
    raw_df.loc[raw_df["Match_Part"].str.startswith("PV", na=False), "Forecast_Loc"] = "10"

    raw_df["CY24_Sales"] = pd.to_numeric(raw_df["CY24_Sales"], errors="coerce").fillna(0)
    raw_df["SOH_Steelfort"] = pd.to_numeric(raw_df["SOH_Steelfort"], errors="coerce").fillna(0)
    raw_df["Ex_China_QTY"] = pd.to_numeric(raw_df["Ex_China_QTY"], errors="coerce").fillna(0)
    raw_df["Purchase_Orders"] = pd.to_numeric(raw_df["Purchase_Orders"], errors="coerce").fillna(0)

    raw_df["Item_Description"] = raw_df["Item_Description"].astype(str).replace("nan", "")
    raw_df["Status"] = raw_df["Status"].astype(str).replace("nan", "")
    raw_df["China_Orders_ETA"] = raw_df["China_Orders_ETA"].astype(str).replace("nan", "")

    return raw_df


@st.cache_data(show_spinner=False)
def load_bunnings_forecast_by_loc_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    raw = load_file_from_bytes(file_bytes, file_name).copy()
    raw.columns = [str(c).replace("\n", " ").strip().lower() for c in raw.columns]

    rename_map = {
        "part_number": "ith_part",
        "part": "ith_part",
        "loc": "ith_loc",
        "location": "ith_loc",
    }
    raw = raw.rename(columns=rename_map)

    if "ith_part" not in raw.columns:
        raise ValueError("Forecasting file must contain 'ith_part' or equivalent part number column.")

    if "ith_loc" not in raw.columns:
        raise ValueError("Forecasting file must contain 'ith_loc' for Bunnings mode.")

    month_cols = [c for c in raw.columns if re.fullmatch(r"ith_\d{2}", c)]
    if not month_cols:
        raise ValueError("Forecasting file must contain monthly columns like ith_01 to ith_24.")

    keep_cols = ["ith_part", "ith_loc"] + month_cols
    raw = raw[keep_cols].copy()

    raw["ith_part"] = normalize_part_number(raw["ith_part"])
    raw["ith_loc"] = raw["ith_loc"].astype(str).str.strip().str.upper()

    raw = raw[(raw["ith_part"] != "") & (raw["ith_part"].str.lower() != "nan")].copy()

    for col in month_cols:
        raw[col] = pd.to_numeric(raw[col], errors="coerce").fillna(0)

    grouped = raw.groupby(["ith_part", "ith_loc"], as_index=False)[month_cols].sum()

    newest_first = get_forecast_month_columns_newest_first(month_cols)
    recent_6_cols = newest_first[:6]

    grouped["Forecast_Monthly_Avg"] = grouped[recent_6_cols].mean(axis=1)
    grouped["Forecast_6m_Avg"] = grouped["Forecast_Monthly_Avg"]

    grouped = grouped.rename(columns={"ith_part": "Match_Part", "ith_loc": "Forecast_Loc"})

    return grouped


def build_bunnings_woh_estimate(
    bunnings_df: pd.DataFrame,
    forecast_loc_df: pd.DataFrame | None,
) -> pd.DataFrame:
    result = bunnings_df.copy()

    if forecast_loc_df is not None:
        result = result.merge(
            forecast_loc_df[["Match_Part", "Forecast_Loc", "Forecast_Monthly_Avg"]],
            on=["Match_Part", "Forecast_Loc"],
            how="left",
        )
    else:
        result["Forecast_Monthly_Avg"] = np.nan

    result["Forecast_Monthly_Avg"] = pd.to_numeric(result["Forecast_Monthly_Avg"], errors="coerce")
    result["Fallback_Monthly_Usage"] = pd.to_numeric(result["CY24_Sales"], errors="coerce").fillna(0) / 12.0

    result["Monthly_Usage_Used"] = np.where(
        result["Forecast_Monthly_Avg"].fillna(0) > 0,
        result["Forecast_Monthly_Avg"],
        result["Fallback_Monthly_Usage"],
    )

    result["Usage_Source"] = np.where(
        result["Forecast_Monthly_Avg"].fillna(0) > 0,
        "Forecast",
        "CY24 Sales Fallback",
    )

    result["Weekly_Usage"] = result["Monthly_Usage_Used"] / 4.33

    result["Weeks_on_Hand"] = np.where(
        result["Weekly_Usage"] > 0,
        result["SOH_Steelfort"] / result["Weekly_Usage"],
        np.nan,
    )

    result["Recommended_Order_4W"] = np.where(
        result["Weekly_Usage"] > 0,
        np.ceil(((result["Weekly_Usage"] * 4) - result["SOH_Steelfort"]).clip(lower=0)),
        0,
    )

    result["Bunnings_Status"] = "🟢 OK"
    result.loc[result["Weeks_on_Hand"].fillna(999999) < 4, "Bunnings_Status"] = "🟠 RISK"
    result.loc[result["Weeks_on_Hand"].fillna(999999) < 1, "Bunnings_Status"] = "🔴 URGENT"

    result["Weekly_Usage"] = result["Weekly_Usage"].round(2)
    result["Weeks_on_Hand"] = result["Weeks_on_Hand"].round(2)
    result["Monthly_Usage_Used"] = result["Monthly_Usage_Used"].round(2)
    result["Forecast_Monthly_Avg"] = result["Forecast_Monthly_Avg"].round(2)
    result["Fallback_Monthly_Usage"] = result["Fallback_Monthly_Usage"].round(2)
    result["Recommended_Order_4W"] = (
        pd.to_numeric(result["Recommended_Order_4W"], errors="coerce").fillna(0).astype(int)
    )

    return result


# =========================================================
# DIALOG
# =========================================================


@st.dialog("Part Details", width="medium")
def show_part_details_dialog(selected_row: pd.Series, demand_basis: str):
    forecast_average_value = selected_row.get(
        "Forecast Average",
        selected_row.get("Demand_Per_Month_Used", 0),
    )

    def format_value(value):
        if pd.isna(value):
            return ""
        if isinstance(value, (int, np.integer)):
            return f"{int(value):,}"
        if isinstance(value, (float, np.floating)):
            if float(value).is_integer():
                return f"{int(value):,}"
            return f"{value:,.2f}"
        return str(value)

    st.markdown(
        """
        <style>
        div[data-testid="stDialog"] section[role="dialog"] {
            max-width: 780px;
        }

        .popup-field {
            background: linear-gradient(180deg, #141a24 0%, #10151d 100%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 14px;
            padding: 12px 14px;
            margin-bottom: 12px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.22);
            min-height: 86px;
        }

        .popup-label {
            font-size: 0.78rem;
            color: #9aa4b2;
            margin-bottom: 6px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }

        .popup-value {
            font-size: 0.98rem;
            color: #f3f4f6;
            word-break: break-word;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(
            f"""
            <div class="popup-field">
                <div class="popup-label">Part #</div>
                <div class="popup-value">{format_value(selected_row.get("Part_Number", ""))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="popup-field">
                <div class="popup-label">Description</div>
                <div class="popup-value">{format_value(selected_row.get("Description", ""))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="popup-field">
                <div class="popup-label">Supplier</div>
                <div class="popup-value">{format_value(selected_row.get("POREF_SUPP", ""))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="popup-field">
                <div class="popup-label">Qty on Order</div>
                <div class="popup-value">{format_value(selected_row.get("Qty on Order", ""))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with col2:
        st.markdown(
            f"""
            <div class="popup-field">
                <div class="popup-label">Available</div>
                <div class="popup-value">{format_value(selected_row.get("Available", ""))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="popup-field">
                <div class="popup-label">Recommended Order</div>
                <div class="popup-value">{format_value(selected_row.get("Recommended Order", ""))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="popup-field">
                <div class="popup-label">EOQ</div>
                <div class="popup-value">{format_value(selected_row.get("EOQ", ""))}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

        st.markdown(
            f"""
            <div class="popup-field">
                <div class="popup-label">Forecast / Avg Used</div>
                <div class="popup-value">{format_value(forecast_average_value)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.caption(f"Demand basis currently in use: {demand_basis}")


# =========================================================
# UI SHELL HELPERS
# =========================================================


def _configure_page() -> None:
    st.set_page_config(
        page_title=APP_TITLE,
        layout="wide",
        initial_sidebar_state="expanded",
    )


def _inject_shell_styles() -> None:
    st.markdown(
        """
        <style>
        .block-container {
            padding-top: 1.4rem;
            padding-bottom: 2rem;
        }

        .app-shell-card {
            border: 1px solid rgba(49, 51, 63, 0.18);
            background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));
            border-radius: 16px;
            padding: 1rem 1.1rem;
            margin-bottom: 0.9rem;
        }

        .app-shell-title {
            font-size: 2rem;
            font-weight: 700;
            line-height: 1.15;
            margin-bottom: 0.35rem;
        }

        .app-shell-subtitle {
            color: rgba(250, 250, 250, 0.78);
            font-size: 0.98rem;
            margin-bottom: 0;
        }

        .app-shell-kicker {
            display: inline-block;
            font-size: 0.78rem;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: #7dd3fc;
            margin-bottom: 0.55rem;
        }

        .app-shell-list {
            margin: 0.35rem 0 0 1.1rem;
            padding: 0;
        }

        .app-shell-list li {
            margin-bottom: 0.3rem;
        }

        div[data-testid="stSidebar"] .stButton button {
            width: 100%;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _init_state() -> None:
    if "selected_worksheet_type" not in st.session_state:
        st.session_state["selected_worksheet_type"] = WORKSHEET_TYPES[0]


def _render_sidebar() -> str:
    with st.sidebar:
        st.markdown("### Workflow")
        worksheet_type = st.radio(
            "Worksheet Type",
            WORKSHEET_TYPES,
            key="selected_worksheet_type",
        )

        st.caption(MODE_DETAILS[worksheet_type]["required"])

        st.markdown("### Current Upload Status")
        if worksheet_type == "Bunnings":
            st.write(f"Bunnings sheet: `{_safe_file_name('bunnings_file_direct')}`")
            st.write(f"Forecast file: `{_safe_file_name('forecast_file_bunnings_direct')}`")
        else:
            st.write(f"Inventory file: `{_safe_file_name('inventory_file')}`")
            st.write(f"Forecast file: `{_safe_file_name('forecast_file')}`")

        left, right = st.columns(2)
        with left:
            if st.button("Clear session", help="Clear uploads and app state."):
                _clear_app_state()
                st.rerun()

        with right:
            if st.button("Reload app", help="Rerun app without clearing uploads."):
                st.rerun()

        with st.expander("Mode notes", expanded=False):
            for item in MODE_DETAILS[worksheet_type]["focus"]:
                st.write(f"- {item}")

    return worksheet_type


def _render_header(worksheet_type: str) -> None:
    subtitle = MODE_DETAILS[worksheet_type]["subtitle"]

    st.markdown(
        f"""
        <div class="app-shell-card">
            <div class="app-shell-kicker">Steelfort forecasting workspace</div>
            <div class="app-shell-title">{APP_TITLE}</div>
            <p class="app-shell-subtitle">{APP_CAPTION}</p>
            <p class="app-shell-subtitle" style="margin-top:0.45rem;">{subtitle}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2, col3 = st.columns([1.2, 1.4, 1.6])
    with col1:
        st.info(f"Mode: {worksheet_type}")
    with col2:
        st.info(MODE_DETAILS[worksheet_type]["required"])
    with col3:
        if worksheet_type == "Bunnings":
            st.info("Output focus: weeks on hand, status, and exportable review.")
        else:
            st.info("Output focus: recommended orders, filtering, and part drilldown.")


def _render_landing_guidance(worksheet_type: str) -> None:
    detail = MODE_DETAILS[worksheet_type]

    left, right = st.columns([1.25, 1])

    with left:
        bullets = "".join(f"<li>{item}</li>" for item in detail["focus"])
        st.markdown(
            f"""
            <div class="app-shell-card">
                <strong>What this mode is for</strong>
                <ul class="app-shell-list">{bullets}</ul>
            </div>
            """,
            unsafe_allow_html=True,
        )

    with right:
        if worksheet_type == "Bunnings":
            workflow_steps = [
                "Upload the Bunnings spreadsheet.",
                "Upload the raw forecast dataset.",
                "Review WOH status and export the filtered output.",
            ]
        else:
            workflow_steps = [
                "Upload the inventory export.",
                "Optionally upload a forecast dataset.",
                "Tune filters and review the resulting order list.",
            ]

        workflow_html = "".join(f"<li>{step}</li>" for step in workflow_steps)
        st.markdown(
            f"""
            <div class="app-shell-card">
                <strong>Recommended flow</strong>
                <ul class="app-shell-list">{workflow_html}</ul>
            </div>
            """,
            unsafe_allow_html=True,
        )


# =========================================================
# INVENTORY VIEW
# =========================================================


def render_inventory_mode(worksheet_type: str) -> None:
    st.subheader(worksheet_type)
    st.caption("Upload your main stock sheet and optionally a forecasting dataset.")

    inventory_file = st.file_uploader(
        "Upload Inventory CSV or Excel",
        type=["csv", "xlsx", "xls"],
        key="inventory_file",
    )

    forecast_file = st.file_uploader(
        "Upload Forecasting CSV or Excel (optional)",
        type=["csv", "xlsx", "xls"],
        key="forecast_file",
    )

    if inventory_file is None and forecast_file is None:
        st.info("Choose a worksheet type, upload the inventory export, then optionally load the forecast dataset.")
        return

    if inventory_file is None:
        st.info("Upload the inventory file to continue.")
        return

    inventory_bytes, inventory_name = get_uploaded_file_bytes(inventory_file)

    with st.spinner("Loading inventory file..."):
        inventory_df = clean_inventory_data_cached(inventory_bytes, inventory_name)

    forecast_df = None
    forecast_loaded = False
    forecast_mode = "Static worksheet averages"

    if forecast_file is not None:
        forecast_bytes, forecast_name = get_uploaded_file_bytes(forecast_file)
        with st.spinner("Loading forecasting file..."):
            forecast_df = load_forecast_history_cached(forecast_bytes, forecast_name)
        forecast_loaded = True
        forecast_mode = "Forecast dataset"

    with st.spinner("Preparing final dataset..."):
        df = merge_inventory_and_forecast(inventory_df, forecast_df)

    if forecast_loaded and "Forecast_3m_Avg" in df.columns:
        df["Forecast Matched?"] = df["Forecast_3m_Avg"].notna()
    else:
        df["Forecast Matched?"] = False

    main_filter_col, main_filter_label = get_filter_config(worksheet_type)

    st.sidebar.markdown("---")
    st.sidebar.header("Ordering Settings")

    table_view = st.sidebar.radio("Table View", ["Simple", "Detailed"], index=0)
    months_target = st.sidebar.number_input("Months Target", min_value=1, value=6)
    only_need_order = st.sidebar.checkbox("Only items needing order", value=True)
    use_eoq_rounding = st.sidebar.checkbox("Round order up to EOQ", value=False)
    exclude_nla = st.sidebar.checkbox("Exclude NLA parts", value=True)

    st.sidebar.markdown("### Cleanup Filters")
    hide_ref_descriptions = st.sidebar.checkbox("Hide superseded/obsolete parts", value=True)
    hide_poc = st.sidebar.checkbox("Hide POC parts", value=False)
    hide_poxpb = st.sidebar.checkbox("Hide POXPB parts", value=False)
    hide_pox = st.sidebar.checkbox("Hide POX parts", value=False)

    show_poxpb = st.sidebar.checkbox("Show ONLY POXPB parts", value=False)
    show_poc_only = st.sidebar.checkbox("Show ONLY POC parts", value=False)
    show_pox_only = st.sidebar.checkbox("Show ONLY POX parts", value=False)
    show_poxcc_only = st.sidebar.checkbox("Show ONLY POXCC parts", value=False)

    custom_forecast_months = 3

    if forecast_loaded:
        demand_basis = st.sidebar.selectbox(
            "Demand Basis",
            ["Custom Forecast Average", "Forecast_Weighted_6m", "6mAvg", "12mAvg"],
        )

        if demand_basis == "Custom Forecast Average":
            custom_forecast_months = st.sidebar.slider(
                "Forecast Months",
                min_value=1,
                max_value=24,
                value=3,
                step=1,
                help="Average forecast demand using the most recent selected number of months.",
            )
    else:
        demand_basis = st.sidebar.selectbox("Demand Basis", ["6mAvg", "12mAvg"])

    month_cols = get_forecast_month_columns_newest_first(df.columns)

    df = apply_inventory_calculations(
        df=df,
        demand_basis=demand_basis,
        months_target=months_target,
        use_eoq_rounding=use_eoq_rounding,
        forecast_loaded=forecast_loaded,
        custom_forecast_months=custom_forecast_months,
        month_cols=month_cols,
    )

    col1, col2, col3, col4, col5 = st.columns([2, 2, 1.5, 1, 1])

    main_filter_values = sorted(
        [x for x in df[main_filter_col].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_main_filters = col1.multiselect(main_filter_label, main_filter_values)

    priorities = sorted(df["Priority V2"].dropna().unique().tolist())
    selected_priorities = col2.multiselect("Priority", priorities, default=priorities)

    location_values = sorted(
        [str(x).strip() for x in df["Loc"].dropna().unique().tolist() if str(x).strip() != ""]
    )

    default_location = ["10"] if worksheet_type == "MTD" and "10" in location_values else []

    selected_locations = col3.multiselect("Location", location_values, default=default_location)
    only_below_min = col4.checkbox("Only below min", value=False)
    only_allocated = col5.checkbox("Only allocated > 0", value=False)

    text_search = st.text_input("Search part number or description")

    filtered = apply_inventory_filters(
        df=df,
        main_filter_col=main_filter_col,
        selected_main_filters=selected_main_filters,
        selected_locations=selected_locations,
        selected_priorities=selected_priorities,
        only_below_min=only_below_min,
        only_allocated=only_allocated,
        exclude_nla=exclude_nla,
        only_need_order=only_need_order,
        text_search=text_search,
        hide_poc=hide_poc,
        hide_poxpb=hide_poxpb,
        hide_pox=hide_pox,
        hide_ref_descriptions=hide_ref_descriptions,
        show_poxpb=show_poxpb,
        show_poc_only=show_poc_only,
        show_pox_only=show_pox_only,
        show_poxcc_only=show_poxcc_only,
    )

    sort_options = [
        "Recommended Order",
        "Base Recommended Order",
        "Qty Allocated",
        "Target Stock",
        "Demand_Per_Month_Used",
        "Forecast Average",
        "Forecast Months Used",
        "Shortage to Min",
        "Shortage to Max",
        "Net After POs",
        "Qty on hand",
        "Qty on Order",
        "Available",
        "Available Now",
        "6mUsage",
        "12mUsage",
        "Forecast_Weighted_6m",
    ]
    sort_options = [c for c in sort_options if c in filtered.columns]

    sort_col = st.selectbox("Sort by", sort_options, index=0 if sort_options else None)
    sort_desc = st.toggle("Descending sort", value=True)

    if not filtered.empty and sort_col:
        filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Rows shown", f"{len(filtered):,}")
    k2.metric("Allocated units", f"{int(filtered['Qty Allocated'].sum()) if not filtered.empty else 0:,}")
    k3.metric("Units to Order", f"{int(filtered['Recommended Order'].sum()) if not filtered.empty else 0:,}")
    k4.metric(
        "Urgent items",
        f"{int((filtered['Priority V2'] == '🔴 URGENT').sum()) if not filtered.empty else 0:,}",
    )
    matched_count = int(df["Forecast Matched?"].sum()) if "Forecast Matched?" in df.columns else 0
    k5.metric("Forecast matches", f"{matched_count:,}")

    if forecast_loaded and demand_basis == "Custom Forecast Average":
        st.caption(
            f"Forecast Mode: {forecast_mode} | Demand Basis: {demand_basis} | "
            f"Forecast Months: {custom_forecast_months} | View: {table_view}"
        )
    else:
        st.caption(f"Forecast Mode: {forecast_mode} | Demand Basis: {demand_basis} | View: {table_view}")

    if worksheet_type == "MTD":
        simple_review_columns = [
            "Part_Number",
            "Description",
            "Type",
            "Loc",
            "Qty on hand",
            "Qty Allocated",
            "Qty on Order",
            "Available",
            "Net After POs",
            "Recommended Order",
            "Priority V2",
        ]
    else:
        simple_review_columns = [
            "Part_Number",
            "Description",
            "POREF_SUPP",
            "Qty on hand",
            "Qty Allocated",
            "Qty on Order",
            "Available",
            "Net After POs",
            "Recommended Order",
            "Priority V2",
        ]

    simple_review_columns = [c for c in simple_review_columns if c in filtered.columns]

    detailed_review_columns = [
        "Part_Number",
        "Description",
        "POREF_SUPP",
        "Type",
        "Loc",
        "Qty on hand",
        "Qty Allocated",
        "Qty on Order",
        "Available",
        "Net After POs",
        "Min",
        "Effective Min",
        "Max",
        "Demand_Per_Month_Used",
        "Forecast Average",
        "Forecast Months Used",
        "Target Stock",
        "Base Recommended Order",
        "Recommended Order",
        "EOQ",
        "6mAvg",
        "6mUsage",
        "12mAvg",
        "12mUsage",
        "Priority V2",
        "Forecast Matched?",
    ]
    detailed_review_columns = [c for c in detailed_review_columns if c in filtered.columns]

    review_columns = simple_review_columns if table_view == "Simple" else detailed_review_columns

    table_df = filtered[review_columns].copy()
    table_df.insert(0, "View", False)
    table_df = table_df.rename(columns={"Priority V2": "Priority"})

    disabled_columns = [c for c in table_df.columns if c != "View"]

    edited_table = st.data_editor(
        table_df,
        use_container_width=True,
        hide_index=True,
        disabled=disabled_columns,
        column_config={
            "View": st.column_config.CheckboxColumn(
                "View",
                help="Tick to open the part details popup",
                default=False,
            )
        },
        key="order_review_table",
    )

    selected_rows = edited_table[edited_table["View"] == True]

    if not selected_rows.empty:
        selected_row = selected_rows.iloc[0].copy()

        if "Priority" in selected_row.index and "Priority V2" not in selected_row.index:
            selected_row["Priority V2"] = selected_row["Priority"]

        show_part_details_dialog(selected_row, demand_basis)

    csv_bytes = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download Order CSV",
        data=csv_bytes,
        file_name="order_list.csv",
        mime="text/csv",
    )

    with st.expander("Detected file structure"):
        st.write("Worksheet Type Selected:", worksheet_type)
        st.write("Main Filter Column:", main_filter_col)
        st.write("Forecast File Loaded:", forecast_loaded)
        st.write("Inventory Rows Loaded:", len(df))
        st.write("Current Table View:", table_view)
        st.write("Inventory Columns Found:")
        st.write(list(inventory_df.columns))

        if forecast_loaded and forecast_df is not None:
            st.write("Forecast Rows Loaded:", len(forecast_df))
            st.write("Forecast Columns Found:")
            st.write(list(forecast_df.columns))
            st.write("Forecast month order used (newest first):")
            st.write(get_forecast_month_columns_newest_first(forecast_df.columns))


# =========================================================
# BUNNINGS VIEW
# =========================================================


def render_bunnings_mode() -> None:
    st.subheader("Bunnings")
    st.caption("Direct Bunnings workflow. Upload the Bunnings spreadsheet and the raw forecasting dataset.")

    bunnings_file = st.file_uploader(
        "Upload Bunnings spreadsheet",
        type=["csv", "xlsx", "xls"],
        key="bunnings_file_direct",
    )

    forecast_file = st.file_uploader(
        "Upload Forecasting CSV or Excel",
        type=["csv", "xlsx", "xls"],
        key="forecast_file_bunnings_direct",
    )

    if bunnings_file is None and forecast_file is None:
        st.info("Choose Bunnings as the worksheet type, then upload the Bunnings sheet and forecast dataset.")
        return

    if bunnings_file is None:
        st.info("Upload the Bunnings spreadsheet to continue.")
        return

    if forecast_file is None:
        st.warning("Upload the raw forecasting dataset as well to calculate forecast-based Weeks on Hand.")
        return

    bunnings_bytes, bunnings_name = get_uploaded_file_bytes(bunnings_file)
    forecast_bytes, forecast_name = get_uploaded_file_bytes(forecast_file)

    with st.spinner("Loading Bunnings spreadsheet..."):
        bunnings_df = clean_bunnings_file_cached(bunnings_bytes, bunnings_name)

    with st.spinner("Loading forecasting data..."):
        bunnings_forecast_df = load_bunnings_forecast_by_loc_cached(forecast_bytes, forecast_name)

    with st.spinner("Calculating Weeks on Hand..."):
        bunnings_view_df = build_bunnings_woh_estimate(bunnings_df, bunnings_forecast_df)

    status_options = ["🔴 URGENT", "🟠 RISK", "🟢 OK"]
    selected_bunnings_status = st.multiselect("Filter status", status_options, default=status_options)

    show_only_matched_loc = st.checkbox("Only show rows with recognised VU / PV location mapping", value=False)
    show_only_active = st.checkbox("Only show non-empty status rows", value=False)
    bunnings_search = st.text_input("Search Bunnings SKU / Steelfort SKU / description", key="bunnings_search_direct")

    filtered = bunnings_view_df.copy()

    if selected_bunnings_status:
        filtered = filtered[filtered["Bunnings_Status"].isin(selected_bunnings_status)]

    if show_only_matched_loc:
        filtered = filtered[filtered["Forecast_Loc"].isin(["DC", "10"])]

    if show_only_active:
        filtered = filtered[filtered["Status"].astype(str).str.strip() != ""]

    if bunnings_search:
        q = bunnings_search.strip().lower()
        filtered = filtered[
            filtered["SKU"].astype(str).str.lower().str.contains(q, na=False)
            | filtered["Steelfort_Sku"].astype(str).str.lower().str.contains(q, na=False)
            | filtered["Match_Part"].astype(str).str.lower().str.contains(q, na=False)
            | filtered["Item_Description"].astype(str).str.lower().str.contains(q, na=False)
        ]

    filtered = filtered.sort_values(
        by=["Weeks_on_Hand", "Weekly_Usage"],
        ascending=[True, False],
        na_position="last",
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows shown", f"{len(filtered):,}")
    m2.metric("Urgent items", f"{int((filtered['Bunnings_Status'] == '🔴 URGENT').sum()):,}")
    m3.metric("Risk items", f"{int((filtered['Bunnings_Status'] == '🟠 RISK').sum()):,}")
    m4.metric(
        "Avg WOH",
        (
            f"{filtered['Weeks_on_Hand'].replace([np.inf, -np.inf], np.nan).mean():.2f}"
            if filtered["Weeks_on_Hand"].notna().any()
            else "0.00"
        ),
    )

    display_columns = [
        "Bunnings_Item_Number",
        "SKU",
        "Steelfort_Sku",
        "Match_Part",
        "Forecast_Loc",
        "Item_Description",
        "Status",
        "CY24_Sales",
        "SOH_Steelfort",
        "Ex_China_QTY",
        "China_Orders_ETA",
        "Forecast_Monthly_Avg",
        "Fallback_Monthly_Usage",
        "Monthly_Usage_Used",
        "Weekly_Usage",
        "Weeks_on_Hand",
        "Bunnings_Status",
        "Recommended_Order_4W",
        "Usage_Source",
    ]
    display_columns = [c for c in display_columns if c in filtered.columns]

    st.dataframe(filtered[display_columns], use_container_width=True, hide_index=True)

    export_csv = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        "Download Bunnings WOH CSV",
        data=export_csv,
        file_name="bunnings_weeks_on_hand.csv",
        mime="text/csv",
    )

    with st.expander("Detected file structure"):
        st.write("Worksheet Type Selected:", "Bunnings")
        st.write("Bunnings Rows Loaded:", len(bunnings_df))
        st.write("Bunnings Columns Found:")
        st.write(list(bunnings_df.columns))
        st.write("Forecast Rows Loaded:", len(bunnings_forecast_df))
        st.write("Forecast Columns Found:")
        st.write(list(bunnings_forecast_df.columns))


# =========================================================
# MAIN
# =========================================================


def _render_mode_view(worksheet_type: str) -> None:
    try:
        if worksheet_type == "Bunnings":
            render_bunnings_mode()
        else:
            render_inventory_mode(worksheet_type)
    except Exception as exc:
        st.error("The selected workflow hit an error while loading or processing the uploaded file.")
        st.caption(
            "Check that the worksheet type matches the file you uploaded and that the source export still follows the expected structure."
        )
        st.exception(exc)


def main() -> None:
    _configure_page()
    _inject_shell_styles()
    _init_state()

    worksheet_type = _render_sidebar()
    _render_header(worksheet_type)
    _render_landing_guidance(worksheet_type)
    _render_mode_view(worksheet_type)


if __name__ == "__main__":
    main()