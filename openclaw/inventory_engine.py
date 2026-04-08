import csv
import io
import re
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd


# =========================================================
# HELPER FUNCTIONS
# =========================================================

def safe_int(value: Any, default: int = 0) -> int:
    """
    Safely convert a value to an integer.
    If conversion fails, return the default value instead.
    """
    try:
        return int(value)
    except Exception:
        return default


def get_filter_config(worksheet_type: str) -> tuple[str, str]:
    """
    Decide which main filter column to use based on worksheet type.

    - MTD sheets filter by Type
    - Other sheets filter by Supplier
    """
    if worksheet_type == "MTD":
        return "Type", "Type"
    return "POREF_SUPP", "Supplier"


def normalize_part_number(series: pd.Series) -> pd.Series:
    """
    Standardise part numbers so inventory and forecast files
    are more likely to merge correctly.

    This:
    - converts to string
    - strips leading/trailing spaces
    - removes repeated internal spaces
    - removes trailing '.0' from Excel-style values
    - converts to uppercase
    """
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .str.replace(r"\.0$", "", regex=True)
        .str.upper()
    )


# =========================================================
# FILE LOADERS
# =========================================================

def read_file_bytes(file_path: str | Path) -> tuple[bytes, str]:
    """
    Read a file from disk and return its bytes + file name.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return path.read_bytes(), path.name


def load_file_from_bytes(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load either a CSV or Excel file from raw bytes.

    This function tries to detect the real header row instead of
    assuming the first row is always the header.
    """
    file_name = file_name.lower()

    # -----------------------------------------------------
    # CSV LOADING
    # -----------------------------------------------------
    if file_name.endswith(".csv"):
        text = file_bytes.decode("utf-8-sig", errors="replace")
        rows = list(csv.reader(io.StringIO(text)))

        header_index = None
        header_markers = {
            "POREF_PART", "Part_Number", "ITMAS_PART", "Part", "PART",
            "Type", "TYPE", "ith_part"
        }

        for i, row in enumerate(rows):
            cleaned = [str(cell).replace("\n", " ").strip() for cell in row]
            if any(cell in header_markers for cell in cleaned):
                header_index = i
                break

        if header_index is None:
            raise ValueError("Could not find a valid header row in the CSV.")

        header = [str(x).replace("\n", " ").strip() for x in rows[header_index]]
        data_rows = rows[header_index + 1:]

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

    # -----------------------------------------------------
    # EXCEL LOADING
    # -----------------------------------------------------
    excel_buffer = io.BytesIO(file_bytes)
    df = pd.read_excel(excel_buffer, header=None)

    header_index = None
    header_markers = {
        "POREF_PART", "Part_Number", "ITMAS_PART", "Part", "PART",
        "Type", "TYPE", "ith_part"
    }

    max_scan = min(30, len(df))
    for i in range(max_scan):
        row_values = [str(x).replace("\n", " ").strip() for x in df.iloc[i].tolist()]
        if any(val in header_markers for val in row_values):
            header_index = i
            break

    if header_index is None:
        raise ValueError("Could not find a valid header row in the Excel file.")

    header = [str(x).replace("\n", " ").strip() for x in df.iloc[header_index].tolist()]
    df = df.iloc[header_index + 1:].copy()
    df.columns = header
    df = df.reset_index(drop=True)

    return df


def load_file_from_path(file_path: str | Path) -> pd.DataFrame:
    """
    Load a CSV or Excel file directly from disk path.
    """
    file_bytes, file_name = read_file_bytes(file_path)
    return load_file_from_bytes(file_bytes, file_name)


# =========================================================
# INVENTORY CLEANING
# =========================================================

def clean_inventory_data(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load and clean the inventory dataset.

    This function:
    - removes junk rows/columns
    - standardises column names
    - fills in missing expected columns
    - converts numeric columns properly
    - calculates stock health fields
    - creates the original Priority field
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


def clean_inventory_data_from_path(file_path: str | Path) -> pd.DataFrame:
    file_bytes, file_name = read_file_bytes(file_path)
    return clean_inventory_data(file_bytes, file_name)


# =========================================================
# FORECAST CLEANING / AGGREGATION
# =========================================================

def load_forecast_history(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load and aggregate forecasting history.

    Expected monthly columns look like:
    ith_01, ith_02, ith_03 ... etc
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

    newest_first = sorted(month_cols, key=lambda x: int(x.split("_")[1]))
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


def load_forecast_history_from_path(file_path: str | Path) -> pd.DataFrame:
    file_bytes, file_name = read_file_bytes(file_path)
    return load_forecast_history(file_bytes, file_name)


def merge_inventory_and_forecast(
    inventory_df: pd.DataFrame,
    forecast_df: pd.DataFrame | None
) -> pd.DataFrame:
    """
    Merge inventory data with forecasting data.
    If no forecast file is provided, just return the inventory data.
    """
    if forecast_df is None:
        return inventory_df.copy()

    return inventory_df.merge(forecast_df, on="Part_Number", how="left")


# =========================================================
# CALCULATION / FILTER ENGINE
# =========================================================

def apply_inventory_logic(
    inventory_df: pd.DataFrame,
    forecast_df: pd.DataFrame | None = None,
    worksheet_type: str = "All Parts",
    months_target: int = 6,
    demand_basis: str = "6mAvg",
    custom_forecast_months: int = 3,
    only_need_order: bool = True,
    use_eoq_rounding: bool = False,
    exclude_nla: bool = True,
    selected_main_filters: list[str] | None = None,
    selected_priorities: list[str] | None = None,
    only_below_min: bool = False,
    only_allocated: bool = False,
    text_search: str = "",
    sort_col: str = "Recommended Order",
    sort_desc: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Main shared engine for both Streamlit UI and API use.
    Returns:
    - filtered dataframe
    - metadata dict
    """
    selected_main_filters = selected_main_filters or []

    df = merge_inventory_and_forecast(inventory_df, forecast_df)

    forecast_loaded = forecast_df is not None
    forecast_mode = "Forecast dataset" if forecast_loaded else "Static worksheet averages"

    if forecast_loaded and "Forecast_3m_Avg" in df.columns:
        df["Forecast Matched?"] = df["Forecast_3m_Avg"].notna()
    else:
        df["Forecast Matched?"] = False

    main_filter_col, main_filter_label = get_filter_config(worksheet_type)

    if "6mAvg" in df.columns:
        df["6mAvg"] = pd.to_numeric(df["6mAvg"], errors="coerce").fillna(0)
    else:
        df["6mAvg"] = 0

    if "12mAvg" in df.columns:
        df["12mAvg"] = pd.to_numeric(df["12mAvg"], errors="coerce").fillna(0)
    else:
        df["12mAvg"] = 0

    month_cols = sorted(
        [c for c in df.columns if re.fullmatch(r"ith_\d{2}", c)],
        key=lambda x: int(x.split("_")[1])
    )

    if month_cols:
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
            np.where(
                df["6mAvg"].fillna(0) > 0,
                df["6mAvg"],
                df["12mAvg"].fillna(0)
            )
        )

    elif forecast_loaded and demand_basis == "Forecast_Weighted_6m":
        if "Forecast_Weighted_6m" not in df.columns:
            df["Forecast_Weighted_6m"] = 0

        df["Forecast_Weighted_6m"] = pd.to_numeric(df["Forecast_Weighted_6m"], errors="coerce").fillna(0)
        df["Forecast Average"] = df["Forecast_Weighted_6m"]
        df["Forecast Months Used"] = 6

        df["Demand_Per_Month_Used"] = np.where(
            df["Forecast_Weighted_6m"].fillna(0) > 0,
            df["Forecast_Weighted_6m"],
            np.where(
                df["6mAvg"].fillna(0) > 0,
                df["6mAvg"],
                df["12mAvg"].fillna(0)
            )
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
        else:
            df["Forecast Months Used"] = 0

    df["Available"] = df["Qty on hand"] - df["Qty Allocated"]
    df["Target Stock"] = df["Demand_Per_Month_Used"] * months_target
    df["Recommended Order"] = np.ceil(df["Target Stock"] - df["Available"]).clip(lower=0)

    if use_eoq_rounding:
        valid_eoq = (df["EOQ"] > 1)
        df["Recommended Order"] = np.where(
            valid_eoq & (df["Recommended Order"] > 0),
            np.ceil(df["Recommended Order"] / df["EOQ"]) * df["EOQ"],
            df["Recommended Order"]
        )

    df["Recommended Order"] = pd.to_numeric(df["Recommended Order"], errors="coerce").fillna(0).astype(int)

    df["Effective Min"] = np.where(
        pd.to_numeric(df["Min"], errors="coerce").fillna(0) > 0,
        pd.to_numeric(df["Min"], errors="coerce").fillna(0),
        5
    )

    df["Effective Min"] = np.where(
        pd.to_numeric(df["Min"], errors="coerce").fillna(0) > 0,
        pd.to_numeric(df["Min"], errors="coerce").fillna(0),
        5
    )

    df["Priority V2"] = "🟢 OK"

    # Option B priority logic based on post-PO stock position:
    # - URGENT if Net After POs is still negative
    # - REPLENISH if Net After POs is non-negative but still below Effective Min
    # - OK if Net After POs meets or exceeds Effective Min
    df.loc[df["Net After POs"] < 0, "Priority V2"] = "🔴 URGENT"

    df.loc[
        (df["Net After POs"] >= 0) &
        (df["Net After POs"] < df["Effective Min"]),
        "Priority V2"
    ] = "🟡 REPLENISH"

    # Second pass override:
    # If the item looked urgent, but incoming POs fully cover the shortage,
    # mark it back to OK.
    df.loc[
        (df["Available"] < 0) &
        (df["Net After POs"] >= 0),
        "Priority V2"
    ] = "🟢 OK"

    if selected_priorities is None:
        selected_priorities = sorted(df["Priority V2"].dropna().unique().tolist())

    filtered = df.copy()

    if selected_main_filters:
        filtered = filtered[filtered[main_filter_col].isin(selected_main_filters)]

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

    sort_options = [
        "Recommended Order",
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

    if sort_col not in sort_options:
        sort_col = "Recommended Order" if "Recommended Order" in sort_options else sort_options[0]

    if not filtered.empty:
        filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    meta = {
        "worksheet_type": worksheet_type,
        "main_filter_col": main_filter_col,
        "main_filter_label": main_filter_label,
        "forecast_loaded": forecast_loaded,
        "forecast_mode": forecast_mode,
        "demand_basis": demand_basis,
        "custom_forecast_months": custom_forecast_months,
        "inventory_rows_loaded": len(df),
        "rows_shown": len(filtered),
        "allocated_units": int(filtered["Qty Allocated"].sum()) if not filtered.empty else 0,
        "units_to_order": int(filtered["Recommended Order"].sum()) if not filtered.empty else 0,
        "urgent_items": int((filtered["Priority V2"] == "🔴 URGENT").sum()) if not filtered.empty else 0,
        "forecast_matches": int(df["Forecast Matched?"].sum()) if "Forecast Matched?" in df.columns else 0,
        "sort_col": sort_col,
        "sort_desc": sort_desc,
    }

    return filtered, meta


# =========================================================
# CONVENIENCE HELPERS FOR API
# =========================================================

def load_and_run(
    inventory_path: str,
    forecast_path: str | None = None,
    worksheet_type: str = "All Parts",
    months_target: int = 6,
    demand_basis: str = "6mAvg",
    custom_forecast_months: int = 3,
    only_need_order: bool = True,
    use_eoq_rounding: bool = False,
    exclude_nla: bool = True,
    selected_main_filters: list[str] | None = None,
    selected_priorities: list[str] | None = None,
    only_below_min: bool = False,
    only_allocated: bool = False,
    text_search: str = "",
    sort_col: str = "Recommended Order",
    sort_desc: bool = True,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    inventory_df = clean_inventory_data_from_path(inventory_path)

    forecast_df = None
    if forecast_path:
        forecast_df = load_forecast_history_from_path(forecast_path)

    return apply_inventory_logic(
        inventory_df=inventory_df,
        forecast_df=forecast_df,
        worksheet_type=worksheet_type,
        months_target=months_target,
        demand_basis=demand_basis,
        custom_forecast_months=custom_forecast_months,
        only_need_order=only_need_order,
        use_eoq_rounding=use_eoq_rounding,
        exclude_nla=exclude_nla,
        selected_main_filters=selected_main_filters,
        selected_priorities=selected_priorities,
        only_below_min=only_below_min,
        only_allocated=only_allocated,
        text_search=text_search,
        sort_col=sort_col,
        sort_desc=sort_desc,
    )


def get_part_details(
    inventory_path: str,
    forecast_path: str | None,
    part_number: str,
    worksheet_type: str = "All Parts",
    months_target: int = 6,
    demand_basis: str = "6mAvg",
    custom_forecast_months: int = 3,
    use_eoq_rounding: bool = False,
    exclude_nla: bool = False,
) -> dict[str, Any] | None:
    """
    Return a single part row after all calculations have been applied.
    """
    inventory_df = clean_inventory_data_from_path(inventory_path)

    forecast_df = None
    if forecast_path:
        forecast_df = load_forecast_history_from_path(forecast_path)

    df, meta = apply_inventory_logic(
        inventory_df=inventory_df,
        forecast_df=forecast_df,
        worksheet_type=worksheet_type,
        months_target=months_target,
        demand_basis=demand_basis,
        custom_forecast_months=custom_forecast_months,
        only_need_order=False,
        use_eoq_rounding=use_eoq_rounding,
        exclude_nla=exclude_nla,
        selected_main_filters=[],
        selected_priorities=[],
        only_below_min=False,
        only_allocated=False,
        text_search="",
        sort_col="Recommended Order",
        sort_desc=True,
    )

    normalized_target = normalize_part_number(pd.Series([part_number])).iloc[0]
    match = df[df["Part_Number"] == normalized_target]

    if match.empty:
        return None

    row = match.iloc[0].replace({np.nan: None}).to_dict()
    row["_meta"] = meta
    return row


def explain_part_decision(
    inventory_path: str,
    forecast_path: str | None,
    part_number: str,
    worksheet_type: str = "All Parts",
    months_target: int = 6,
    demand_basis: str = "6mAvg",
    custom_forecast_months: int = 3,
    use_eoq_rounding: bool = False,
    exclude_nla: bool = False,
) -> dict[str, Any] | None:
    """
    Return a simplified explanation payload for a single part.
    """
    details = get_part_details(
        inventory_path=inventory_path,
        forecast_path=forecast_path,
        part_number=part_number,
        worksheet_type=worksheet_type,
        months_target=months_target,
        demand_basis=demand_basis,
        custom_forecast_months=custom_forecast_months,
        use_eoq_rounding=use_eoq_rounding,
        exclude_nla=exclude_nla,
    )

    if details is None:
        return None

    explanation = {
        "Part_Number": details.get("Part_Number"),
        "Description": details.get("Description"),
        "Supplier": details.get("POREF_SUPP"),
        "Type": details.get("Type"),
        "Qty_on_hand": details.get("Qty on hand"),
        "Qty_allocated": details.get("Qty Allocated"),
        "Qty_on_order": details.get("Qty on Order"),
        "Available": details.get("Available"),
        "Net_After_POs": details.get("Net After POs"),
        "Min": details.get("Min"),
        "Effective_Min": details.get("Effective Min"),
        "Max": details.get("Max"),
        "Demand_Per_Month_Used": details.get("Demand_Per_Month_Used"),
        "Forecast_Average": details.get("Forecast Average"),
        "Forecast_Months_Used": details.get("Forecast Months Used"),
        "Target_Stock": details.get("Target Stock"),
        "Recommended_Order": details.get("Recommended Order"),
        "EOQ": details.get("EOQ"),
        "Priority_V2": details.get("Priority V2"),
        "Forecast_Matched": details.get("Forecast Matched?"),
        "Reasoning": {
            "available_rule": "Available = Qty on hand - Qty Allocated",
            "target_stock_rule": f"Target Stock = Demand_Per_Month_Used x Months Target ({months_target})",
            "recommended_order_rule": "Recommended Order = ceil(Target Stock - Available), minimum 0",
            "priority_rule": (
                "URGENT if Net After POs < 0; "
                "REPLENISH if Net After POs >= 0 and Net After POs < Effective Min; "
                "otherwise OK"
            ),
        },
    }

    return explanation