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
APP_CAPTION = "Version 9.0.0 - Unified single-file Streamlit app"

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
    "selected_part_for_chart",
]

REVIEW_PRESETS = [
    "Balanced Review",
    "Urgent Buy Review",
    "Allocated Pressure Review",
    "Supplier Order Review",
    "Low Noise Review",
    "All Rows",
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


def _safe_divide(numerator, denominator):
    denominator = pd.to_numeric(denominator, errors="coerce")
    numerator = pd.to_numeric(numerator, errors="coerce")
    return np.where(denominator > 0, numerator / denominator, np.nan)


# =========================================================
# FILE LOADING
# =========================================================


@st.cache_data(show_spinner=False)
def load_file_from_bytes(file_bytes: bytes, file_name: str) -> pd.DataFrame:
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
def load_forecast_history_cached(file_bytes: bytes, file_name: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
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

    month_order = get_forecast_month_columns_newest_first(month_cols)

    grouped = raw.groupby("ith_part", as_index=False)[month_cols].sum()

    newest_3 = month_order[:3]
    newest_6 = month_order[:6]
    newest_12 = month_order[:12]

    grouped["Forecast_3m_Total"] = grouped[newest_3].sum(axis=1)
    grouped["Forecast_6m_Total"] = grouped[newest_6].sum(axis=1)
    grouped["Forecast_12m_Total"] = grouped[newest_12].sum(axis=1)

    grouped["Forecast_3m_Avg"] = grouped[newest_3].mean(axis=1)
    grouped["Forecast_6m_Avg"] = grouped[newest_6].mean(axis=1)
    grouped["Forecast_12m_Avg"] = grouped[newest_12].mean(axis=1)

    weights = np.array([6, 5, 4, 3, 2, 1], dtype=float)
    grouped["Forecast_Weighted_6m"] = (grouped[newest_6].to_numpy(dtype=float) * weights).sum(axis=1) / weights.sum()

    grouped = grouped.rename(columns={"ith_part": "Part_Number"})
    grouped["Part_Number"] = normalize_part_number(grouped["Part_Number"])

    detail = raw.rename(columns={"ith_part": "Part_Number"}).copy()
    detail["Part_Number"] = normalize_part_number(detail["Part_Number"])

    return grouped, detail, month_order


def merge_inventory_and_forecast(inventory_df: pd.DataFrame, forecast_df: pd.DataFrame | None) -> pd.DataFrame:
    if forecast_df is None:
        return inventory_df.copy()
    return inventory_df.merge(forecast_df, on="Part_Number", how="left")


# =========================================================
# INVENTORY SERVICES
# =========================================================


@st.cache_data(show_spinner=False)
def clean_inventory_data_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
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
    df = df.copy()

    df["6mAvg"] = pd.to_numeric(df.get("6mAvg", 0), errors="coerce").fillna(0)
    df["12mAvg"] = pd.to_numeric(df.get("12mAvg", 0), errors="coerce").fillna(0)

    for col in month_cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0)

    df["Forecast Average"] = 0.0
    df["Forecast Months Used"] = 0
    df["Demand_Source"] = "Worksheet Average"

    if forecast_loaded and demand_basis == "Custom Forecast Average" and month_cols:
        selected_month_cols = month_cols[:custom_forecast_months]
        df["Forecast Average"] = df[selected_month_cols].mean(axis=1)
        df["Forecast Months Used"] = len(selected_month_cols)

        use_forecast = df["Forecast Average"].fillna(0) > 0
        use_6m = (~use_forecast) & (df["6mAvg"].fillna(0) > 0)

        df["Demand_Per_Month_Used"] = np.where(
            use_forecast,
            df["Forecast Average"],
            np.where(use_6m, df["6mAvg"], df["12mAvg"].fillna(0)),
        )
        df["Demand_Source"] = np.where(
            use_forecast,
            f"Forecast Avg ({len(selected_month_cols)}m)",
            np.where(use_6m, "Worksheet 6mAvg", "Worksheet 12mAvg"),
        )

    elif forecast_loaded and demand_basis == "Forecast_Weighted_6m":
        df["Forecast_Weighted_6m"] = pd.to_numeric(
            df.get("Forecast_Weighted_6m", 0), errors="coerce"
        ).fillna(0)
        df["Forecast Average"] = df["Forecast_Weighted_6m"]
        df["Forecast Months Used"] = 6

        use_forecast = df["Forecast_Weighted_6m"].fillna(0) > 0
        use_6m = (~use_forecast) & (df["6mAvg"].fillna(0) > 0)

        df["Demand_Per_Month_Used"] = np.where(
            use_forecast,
            df["Forecast_Weighted_6m"],
            np.where(use_6m, df["6mAvg"], df["12mAvg"].fillna(0)),
        )
        df["Demand_Source"] = np.where(
            use_forecast,
            "Forecast Weighted 6m",
            np.where(use_6m, "Worksheet 6mAvg", "Worksheet 12mAvg"),
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

        df["Demand_Source"] = f"Worksheet {demand_basis}"

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

    df["Cover_Months_After_PO"] = _safe_divide(df["Net After POs"], df["Demand_Per_Month_Used"])
    df["On_Order_Covers_Target?"] = df["Net After POs"] >= df["Target Stock"]
    df["Has_Demand?"] = df["Demand_Per_Month_Used"] > 0

    df["Demand_Gap_to_Target"] = (df["Target Stock"] - df["Net After POs"]).clip(lower=0)

    df["Decision Summary"] = "OK - enough stock/inbound cover"
    df.loc[df["Has_Demand?"] == False, "Decision Summary"] = "Low confidence - zero demand basis"
    df.loc[(df["Priority V2"] == "🟡 REPLENISH"), "Decision Summary"] = "Below effective minimum"
    df.loc[(df["Priority V2"] == "🔴 URGENT"), "Decision Summary"] = "Negative net stock after POs"
    df.loc[
        (df["Recommended Order"] > 0) & (df["Target Stock"] > df["Net After POs"]),
        "Decision Summary"
    ] = "Order required to reach target cover"
    df.loc[
        (df["Recommended Order"] == 0) & (df["On_Order_Covers_Target?"]),
        "Decision Summary"
    ] = "Existing stock and POs already cover target"

    df["Decision Reason"] = (
        "Demand source: " + df["Demand_Source"].astype(str)
        + " | Target: " + df["Target Stock"].round(2).astype(str)
        + " | Net after POs: " + df["Net After POs"].round(2).astype(str)
    )

    zero_minmax = (df["Min"] <= 0) & (df["Max"] <= 0)
    zero_eoq = df["EOQ"] <= 0
    no_demand = df["Demand_Per_Month_Used"] <= 0
    missing_forecast = forecast_loaded & (~df.get("Forecast_3m_Avg", pd.Series(index=df.index, dtype=float)).notna())
    invalid_moh = df["Months on Hand Numeric"].isna() & df["Months on Hand"].astype(str).str.contains("DIV", case=False, na=False)
    over_ordered = (df["Qty on Order"] > 0) & (df["Demand_Per_Month_Used"] > 0) & ((df["Qty on Order"] / df["Demand_Per_Month_Used"]) > 12)

    flags = []
    for idx in df.index:
        row_flags = []
        if zero_minmax.loc[idx]:
            row_flags.append("No min/max")
        if zero_eoq.loc[idx]:
            row_flags.append("No EOQ")
        if no_demand.loc[idx]:
            row_flags.append("No demand")
        if forecast_loaded and missing_forecast.loc[idx]:
            row_flags.append("No forecast match")
        if invalid_moh.loc[idx]:
            row_flags.append("Invalid MOH")
        if over_ordered.loc[idx]:
            row_flags.append("High on-order vs demand")
        if df.loc[idx, "Is NLA?"]:
            row_flags.append("NLA description")
        flags.append(" | ".join(row_flags) if row_flags else "Clean")

    df["Data Quality Flags"] = flags
    df["Data Quality Score"] = df["Data Quality Flags"].apply(lambda x: 0 if x == "Clean" else len(str(x).split(" | ")))
    df["Confidence"] = "High"
    df.loc[df["Data Quality Score"] >= 1, "Confidence"] = "Medium"
    df.loc[df["Data Quality Score"] >= 3, "Confidence"] = "Low"

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


def build_supplier_summary(df: pd.DataFrame, main_filter_col: str) -> pd.DataFrame:
    if df.empty or main_filter_col not in df.columns:
        return pd.DataFrame()

    summary = (
        df.groupby(main_filter_col, dropna=False)
        .agg(
            Rows=("Part_Number", "count"),
            Need_Order=("Recommended Order", lambda s: int((pd.to_numeric(s, errors="coerce").fillna(0) > 0).sum())),
            Urgent=("Priority V2", lambda s: int((s == "🔴 URGENT").sum())),
            Replenish=("Priority V2", lambda s: int((s == "🟡 REPLENISH").sum())),
            Recommended_Qty=("Recommended Order", "sum"),
            Allocated_Qty=("Qty Allocated", "sum"),
            Demand_Per_Month=("Demand_Per_Month_Used", "sum"),
        )
        .reset_index()
        .rename(columns={main_filter_col: "Group"})
        .sort_values(["Recommended_Qty", "Urgent", "Need_Order"], ascending=[False, False, False])
    )
    return summary


def build_inventory_export(df: pd.DataFrame, export_type: str) -> tuple[pd.DataFrame, str]:
    if export_type == "Buyer Review":
        cols = [
            "Part_Number",
            "Description",
            "POREF_SUPP",
            "Type",
            "Loc",
            "Qty on hand",
            "Qty Allocated",
            "Qty on Order",
            "Net After POs",
            "Demand_Per_Month_Used",
            "Target Stock",
            "Recommended Order",
            "Priority V2",
            "Decision Summary",
            "Data Quality Flags",
            "Confidence",
        ]
        cols = [c for c in cols if c in df.columns]
        return df[cols].copy(), "buyer_review_export.csv"

    if export_type == "Supplier PO Prep":
        cols = [
            "POREF_SUPP",
            "Part_Number",
            "Description",
            "Recommended Order",
            "Qty on hand",
            "Qty Allocated",
            "Qty on Order",
            "Net After POs",
            "Demand_Per_Month_Used",
            "Decision Summary",
        ]
        cols = [c for c in cols if c in df.columns]
        export_df = df[cols].copy()
        export_df = export_df[export_df.get("Recommended Order", 0) > 0]
        return export_df, "supplier_po_prep.csv"

    if export_type == "Exception Report":
        export_df = df[
            (df["Recommended Order"] > 0)
            | (df["Priority V2"] == "🔴 URGENT")
            | (df["Data Quality Score"] > 0)
            | (df["Qty Allocated"] > 0)
        ].copy()
        cols = [
            "Part_Number",
            "Description",
            "POREF_SUPP",
            "Type",
            "Loc",
            "Recommended Order",
            "Priority V2",
            "Decision Summary",
            "Data Quality Flags",
            "Confidence",
        ]
        cols = [c for c in cols if c in export_df.columns]
        return export_df[cols], "exception_report.csv"

    return df.copy(), "full_inventory_export.csv"


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
def load_bunnings_forecast_by_loc_cached(file_bytes: bytes, file_name: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
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

    month_order = get_forecast_month_columns_newest_first(month_cols)

    grouped = raw.groupby(["ith_part", "ith_loc"], as_index=False)[month_cols].sum()
    recent_6_cols = month_order[:6]
    grouped["Forecast_Monthly_Avg"] = grouped[recent_6_cols].mean(axis=1)
    grouped["Forecast_6m_Avg"] = grouped["Forecast_Monthly_Avg"]

    grouped = grouped.rename(columns={"ith_part": "Match_Part", "ith_loc": "Forecast_Loc"})
    detail = raw.rename(columns={"ith_part": "Match_Part", "ith_loc": "Forecast_Loc"}).copy()

    return grouped, detail, month_order


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

    result["Location_Match"] = np.where(
        result["Forecast_Loc"].isin(["DC", "10"]),
        "Mapped",
        "Unmapped",
    )
    result["Usage_Confidence"] = "High"
    result.loc[result["Usage_Source"] == "CY24 Sales Fallback", "Usage_Confidence"] = "Medium"
    result.loc[
        (result["Usage_Source"] == "CY24 Sales Fallback") & (result["Forecast_Loc"] == ""),
        "Usage_Confidence"
    ] = "Low"

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

    result["Data Quality Flags"] = ""
    result.loc[result["Monthly_Usage_Used"] <= 0, "Data Quality Flags"] = "No usable demand"
    result.loc[
        (result["Data Quality Flags"] == "") & (result["Usage_Source"] == "CY24 Sales Fallback"),
        "Data Quality Flags"
    ] = "Fallback used"
    result.loc[
        (result["Data Quality Flags"] != "") & (result["Usage_Source"] == "CY24 Sales Fallback"),
        "Data Quality Flags"
    ] = result["Data Quality Flags"] + " | Fallback used"
    result.loc[
        (result["Data Quality Flags"] != "") & (result["Location_Match"] == "Unmapped"),
        "Data Quality Flags"
    ] = result["Data Quality Flags"] + " | Unmapped location"
    result.loc[
        (result["Data Quality Flags"] == "") & (result["Location_Match"] == "Unmapped"),
        "Data Quality Flags"
    ] = "Unmapped location"
    result.loc[result["Data Quality Flags"] == "", "Data Quality Flags"] = "Clean"

    result["Weekly_Usage"] = result["Weekly_Usage"].round(2)
    result["Weeks_on_Hand"] = result["Weeks_on_Hand"].round(2)
    result["Monthly_Usage_Used"] = result["Monthly_Usage_Used"].round(2)
    result["Forecast_Monthly_Avg"] = result["Forecast_Monthly_Avg"].round(2)
    result["Fallback_Monthly_Usage"] = result["Fallback_Monthly_Usage"].round(2)
    result["Recommended_Order_4W"] = pd.to_numeric(
        result["Recommended_Order_4W"], errors="coerce"
    ).fillna(0).astype(int)

    return result


# =========================================================
# DIALOG
# =========================================================


@st.dialog("Part Details", width="large")
def show_part_details_dialog(selected_row: pd.Series, demand_basis: str):
    def field(label: str, value):
        st.markdown(
            f"""
            <div style="background:#141a24;border:1px solid rgba(255,255,255,0.08);border-radius:14px;padding:12px 14px;margin-bottom:12px;">
                <div style="font-size:0.78rem;color:#9aa4b2;margin-bottom:6px;font-weight:700;text-transform:uppercase;letter-spacing:0.03em;">{label}</div>
                <div style="font-size:0.98rem;color:#f3f4f6;word-break:break-word;">{format_numeric_display(value)}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    left, right = st.columns(2)

    with left:
        field("Part #", selected_row.get("Part_Number", ""))
        field("Description", selected_row.get("Description", ""))
        field("Supplier", selected_row.get("POREF_SUPP", ""))
        field("Type", selected_row.get("Type", ""))
        field("Location", selected_row.get("Loc", ""))
        field("Decision Summary", selected_row.get("Decision Summary", ""))
        field("Decision Reason", selected_row.get("Decision Reason", ""))

    with right:
        field("Qty on Hand", selected_row.get("Qty on hand", ""))
        field("Qty Allocated", selected_row.get("Qty Allocated", ""))
        field("Qty on Order", selected_row.get("Qty on Order", ""))
        field("Net After POs", selected_row.get("Net After POs", ""))
        field("Demand / Month Used", selected_row.get("Demand_Per_Month_Used", ""))
        field("Target Stock", selected_row.get("Target Stock", ""))
        field("Recommended Order", selected_row.get("Recommended Order", ""))
        field("Confidence", selected_row.get("Confidence", ""))
        field("Data Quality Flags", selected_row.get("Data Quality Flags", ""))

    st.caption(f"Demand basis in use: {demand_basis}")


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
        .block-container { padding-top: 1.4rem; padding-bottom: 2rem; }
        .app-shell-card {
            border: 1px solid rgba(49, 51, 63, 0.18);
            background: linear-gradient(180deg, rgba(255,255,255,0.02), rgba(255,255,255,0.01));
            border-radius: 16px; padding: 1rem 1.1rem; margin-bottom: 0.9rem;
        }
        .app-shell-title { font-size: 2rem; font-weight: 700; line-height: 1.15; margin-bottom: 0.35rem; }
        .app-shell-subtitle { color: rgba(250, 250, 250, 0.78); font-size: 0.98rem; margin-bottom: 0; }
        .app-shell-kicker {
            display: inline-block; font-size: 0.78rem; font-weight: 700; text-transform: uppercase;
            letter-spacing: 0.04em; color: #7dd3fc; margin-bottom: 0.55rem;
        }
        .app-shell-list { margin: 0.35rem 0 0 1.1rem; padding: 0; }
        .app-shell-list li { margin-bottom: 0.3rem; }
        div[data-testid="stSidebar"] .stButton button { width: 100%; }
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
            st.info("Output focus: weeks on hand, usage confidence, and exportable review.")
        else:
            st.info("Output focus: recommendation quality, noise reduction, and decision visibility.")


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
                "Review WOH, confidence, and export the filtered output.",
            ]
        else:
            workflow_steps = [
                "Upload the inventory export.",
                "Optionally upload a forecast dataset.",
                "Use a review preset, validate the output, then export the list you need.",
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
    forecast_detail = None
    forecast_loaded = False
    forecast_mode = "Static worksheet averages"
    month_cols = []

    if forecast_file is not None:
        forecast_bytes, forecast_name = get_uploaded_file_bytes(forecast_file)
        with st.spinner("Loading forecasting file..."):
            forecast_df, forecast_detail, month_cols = load_forecast_history_cached(forecast_bytes, forecast_name)
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
    st.sidebar.header("Review Settings")

    review_preset = st.sidebar.selectbox("Review Preset", REVIEW_PRESETS, index=0)
    table_view = st.sidebar.radio("Table View", ["Simple", "Detailed"], index=0)
    months_target = st.sidebar.number_input("Months Target", min_value=1, value=6)

    if review_preset == "Balanced Review":
        only_need_order_default = True
        exclude_nla_default = True
        only_allocated_default = False
        only_below_min_default = False
    elif review_preset == "Urgent Buy Review":
        only_need_order_default = True
        exclude_nla_default = True
        only_allocated_default = True
        only_below_min_default = False
    elif review_preset == "Allocated Pressure Review":
        only_need_order_default = False
        exclude_nla_default = True
        only_allocated_default = True
        only_below_min_default = False
    elif review_preset == "Supplier Order Review":
        only_need_order_default = True
        exclude_nla_default = True
        only_allocated_default = False
        only_below_min_default = False
    elif review_preset == "Low Noise Review":
        only_need_order_default = True
        exclude_nla_default = True
        only_allocated_default = False
        only_below_min_default = True
    else:
        only_need_order_default = False
        exclude_nla_default = False
        only_allocated_default = False
        only_below_min_default = False

    only_need_order = st.sidebar.checkbox("Only items needing order", value=only_need_order_default)
    use_eoq_rounding = st.sidebar.checkbox("Round order up to EOQ", value=False)
    exclude_nla = st.sidebar.checkbox("Exclude NLA parts", value=exclude_nla_default)

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

    if not month_cols:
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

    top_left, top_right = st.columns([1.2, 1])
    with top_left:
        st.caption(f"Forecast Mode: {forecast_mode} | Demand Basis: {demand_basis} | Review Preset: {review_preset}")
    with top_right:
        noisy_rows = int((df["Data Quality Score"] > 0).sum()) if "Data Quality Score" in df.columns else 0
        st.caption(f"Rows with quality flags: {noisy_rows:,}")

    col1, col2, col3, col4, col5 = st.columns([2, 2, 1.5, 1, 1])

    main_filter_values = sorted(
        [x for x in df[main_filter_col].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_main_filters = col1.multiselect(main_filter_label, main_filter_values)

    priorities = sorted(df["Priority V2"].dropna().unique().tolist())
    if review_preset == "Urgent Buy Review":
        default_priorities = ["🔴 URGENT", "🟡 REPLENISH"] if "🟡 REPLENISH" in priorities else priorities
    elif review_preset == "Allocated Pressure Review":
        default_priorities = priorities
    elif review_preset == "Low Noise Review":
        default_priorities = ["🔴 URGENT", "🟡 REPLENISH"] if "🟡 REPLENISH" in priorities else priorities
    else:
        default_priorities = priorities

    selected_priorities = col2.multiselect("Priority", priorities, default=default_priorities)

    location_values = sorted(
        [str(x).strip() for x in df["Loc"].dropna().unique().tolist() if str(x).strip() != ""]
    )
    default_location = ["10"] if worksheet_type == "MTD" and "10" in location_values else []
    selected_locations = col3.multiselect("Location", location_values, default=default_location)
    only_below_min = col4.checkbox("Only below min", value=only_below_min_default)
    only_allocated = col5.checkbox("Only allocated > 0", value=only_allocated_default)

    text_search = st.text_input("Search part number or description")
    show_only_quality_issues = st.checkbox("Only rows with data quality flags", value=False)
    show_only_with_demand = st.checkbox("Only rows with positive demand", value=(review_preset != "All Rows"))
    confidence_filter = st.multiselect("Confidence", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])

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

    if show_only_quality_issues:
        filtered = filtered[filtered["Data Quality Score"] > 0]

    if show_only_with_demand:
        filtered = filtered[filtered["Demand_Per_Month_Used"] > 0]

    if confidence_filter:
        filtered = filtered[filtered["Confidence"].isin(confidence_filter)]

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
        "Cover_Months_After_PO",
        "Data Quality Score",
        "6mUsage",
        "12mUsage",
        "Forecast_Weighted_6m",
    ]
    sort_options = [c for c in sort_options if c in filtered.columns]

    sort_col = st.selectbox("Sort by", sort_options, index=0 if sort_options else None)
    sort_desc = st.toggle("Descending sort", value=True)

    if not filtered.empty and sort_col:
        filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Rows shown", f"{len(filtered):,}")
    k2.metric("Allocated units", f"{int(filtered['Qty Allocated'].sum()) if not filtered.empty else 0:,}")
    k3.metric("Units to Order", f"{int(filtered['Recommended Order'].sum()) if not filtered.empty else 0:,}")
    k4.metric("Urgent items", f"{int((filtered['Priority V2'] == '🔴 URGENT').sum()) if not filtered.empty else 0:,}")
    matched_count = int(df["Forecast Matched?"].sum()) if "Forecast Matched?" in df.columns else 0
    k5.metric("Forecast matches", f"{matched_count:,}")
    k6.metric("Flagged rows", f"{int((filtered['Data Quality Score'] > 0).sum()) if not filtered.empty else 0:,}")

    supplier_summary = build_supplier_summary(filtered, main_filter_col)
    with st.expander("Supplier / Group Summary", expanded=False):
        if supplier_summary.empty:
            st.info("No grouped summary available for the current filters.")
        else:
            st.dataframe(supplier_summary, use_container_width=True, hide_index=True)

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
            "Decision Summary",
            "Confidence",
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
            "Decision Summary",
            "Confidence",
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
        "Demand_Source",
        "Demand_Per_Month_Used",
        "Forecast Average",
        "Forecast Months Used",
        "Target Stock",
        "Base Recommended Order",
        "Recommended Order",
        "EOQ",
        "Cover_Months_After_PO",
        "Shortage to Min",
        "Shortage to Max",
        "6mAvg",
        "6mUsage",
        "12mAvg",
        "12mUsage",
        "Priority V2",
        "Decision Summary",
        "Decision Reason",
        "Data Quality Flags",
        "Confidence",
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

    selected_part_number = None
    if not selected_rows.empty:
        selected_row = selected_rows.iloc[0].copy()
        if "Priority" in selected_row.index and "Priority V2" not in selected_row.index:
            selected_row["Priority V2"] = selected_row["Priority"]
        selected_part_number = selected_row.get("Part_Number")
        show_part_details_dialog(selected_row, demand_basis)

    with st.expander("Demand Trend Preview", expanded=False):
        if forecast_loaded and forecast_detail is not None and not forecast_detail.empty:
            available_parts = filtered["Part_Number"].dropna().astype(str).unique().tolist()
            available_parts = sorted([p for p in available_parts if p.strip() != ""])
            default_part = selected_part_number if selected_part_number in available_parts else (available_parts[0] if available_parts else None)

            if default_part is None:
                st.info("No filtered parts available for trend preview.")
            else:
                chart_part = st.selectbox(
                    "Select part for trend preview",
                    available_parts,
                    index=available_parts.index(default_part),
                    key="selected_part_for_chart",
                )
                chart_rows = forecast_detail[forecast_detail["Part_Number"] == chart_part].copy()

                if chart_rows.empty:
                    st.info("No forecast history found for the selected part.")
                else:
                    trend = chart_rows[month_cols].sum(axis=0)
                    trend_df = pd.DataFrame(
                        {
                            "Month": list(reversed(month_cols)),
                            "Usage": list(reversed(trend.values.tolist())),
                        }
                    )
                    st.line_chart(trend_df.set_index("Month"))
                    st.caption(
                        f"Trend preview for {chart_part}. Left side is older months; "
                        f"right side is the most recent month from the forecast export."
                    )
        else:
            st.info("Upload a forecast file to enable trend preview.")

    export_type = st.selectbox(
        "Export Type",
        ["Full Detailed Export", "Buyer Review", "Supplier PO Prep", "Exception Report"],
        index=1,
    )
    export_df, export_name = build_inventory_export(filtered, export_type)
    export_csv = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        f"Download {export_type} CSV",
        data=export_csv,
        file_name=export_name,
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
            st.write(month_cols)


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
        bunnings_forecast_df, bunnings_forecast_detail, bunnings_month_cols = load_bunnings_forecast_by_loc_cached(forecast_bytes, forecast_name)

    with st.spinner("Calculating Weeks on Hand..."):
        bunnings_view_df = build_bunnings_woh_estimate(bunnings_df, bunnings_forecast_df)

    status_options = ["🔴 URGENT", "🟠 RISK", "🟢 OK"]
    selected_bunnings_status = st.multiselect("Filter status", status_options, default=status_options)

    show_only_matched_loc = st.checkbox("Only show rows with recognised VU / PV location mapping", value=False)
    show_only_active = st.checkbox("Only show non-empty status rows", value=False)
    only_quality_issues = st.checkbox("Only rows with quality flags", value=False)
    usage_confidence_filter = st.multiselect("Usage Confidence", ["High", "Medium", "Low"], default=["High", "Medium", "Low"])
    bunnings_search = st.text_input("Search Bunnings SKU / Steelfort SKU / description", key="bunnings_search_direct")

    filtered = bunnings_view_df.copy()

    if selected_bunnings_status:
        filtered = filtered[filtered["Bunnings_Status"].isin(selected_bunnings_status)]

    if show_only_matched_loc:
        filtered = filtered[filtered["Forecast_Loc"].isin(["DC", "10"])]

    if show_only_active:
        filtered = filtered[filtered["Status"].astype(str).str.strip() != ""]

    if only_quality_issues:
        filtered = filtered[filtered["Data Quality Flags"] != "Clean"]

    if usage_confidence_filter:
        filtered = filtered[filtered["Usage_Confidence"].isin(usage_confidence_filter)]

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

    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Rows shown", f"{len(filtered):,}")
    m2.metric("Urgent items", f"{int((filtered['Bunnings_Status'] == '🔴 URGENT').sum()):,}")
    m3.metric("Risk items", f"{int((filtered['Bunnings_Status'] == '🟠 RISK').sum()):,}")
    m4.metric(
        "Avg WOH",
        f"{filtered['Weeks_on_Hand'].replace([np.inf, -np.inf], np.nan).mean():.2f}"
        if filtered["Weeks_on_Hand"].notna().any() else "0.00"
    )
    m5.metric("Fallback rows", f"{int((filtered['Usage_Source'] == 'CY24 Sales Fallback').sum()):,}")

    display_columns = [
        "Bunnings_Item_Number",
        "SKU",
        "Steelfort_Sku",
        "Match_Part",
        "Forecast_Loc",
        "Location_Match",
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
        "Usage_Confidence",
        "Data Quality Flags",
    ]
    display_columns = [c for c in display_columns if c in filtered.columns]

    st.dataframe(filtered[display_columns], use_container_width=True, hide_index=True)

    with st.expander("Bunnings Trend Preview", expanded=False):
        available_parts = filtered["Match_Part"].dropna().astype(str).unique().tolist()
        available_parts = sorted([p for p in available_parts if p.strip() != ""])
        if available_parts:
            selected_part = st.selectbox("Select part for Bunnings trend preview", available_parts, index=0)
            chart_rows = bunnings_forecast_detail[bunnings_forecast_detail["Match_Part"] == selected_part].copy()
            if chart_rows.empty:
                st.info("No forecast history found for the selected part.")
            else:
                trend = chart_rows[bunnings_month_cols].sum(axis=0)
                trend_df = pd.DataFrame(
                    {
                        "Month": list(reversed(bunnings_month_cols)),
                        "Usage": list(reversed(trend.values.tolist())),
                    }
                )
                st.line_chart(trend_df.set_index("Month"))
        else:
            st.info("No filtered parts available for trend preview.")

    export_variant = st.selectbox(
        "Export Type",
        ["Full Bunnings Export", "Bunnings Exception Report"],
        index=1,
    )
    if export_variant == "Bunnings Exception Report":
        export_df = filtered[
            (filtered["Bunnings_Status"].isin(["🔴 URGENT", "🟠 RISK"]))
            | (filtered["Data Quality Flags"] != "Clean")
        ].copy()
        export_name = "bunnings_exception_report.csv"
    else:
        export_df = filtered.copy()
        export_name = "bunnings_weeks_on_hand.csv"

    export_csv = export_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        f"Download {export_variant} CSV",
        data=export_csv,
        file_name=export_name,
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