import re
import numpy as np
import pandas as pd
import streamlit as st

from services.file_loader import load_file_from_bytes
from utils.helpers import normalize_part_number, get_forecast_month_columns_newest_first


@st.cache_data(show_spinner=False)
def clean_bunnings_file_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    Load and clean the Bunnings file.
    """
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
        raw_df["SKU"]
    )
    raw_df["Match_Part"] = normalize_part_number(pd.Series(raw_df["Match_Part"]))

    raw_df = raw_df[
        (raw_df["Match_Part"] != "") &
        (raw_df["Match_Part"].str.lower() != "nan")
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
    """
    Load raw forecast history and aggregate by part + location.
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
        raise ValueError("Forecasting file must contain 'ith_loc' for Bunnings mode.")

    month_cols = [c for c in raw.columns if re.fullmatch(r"ith_\d{2}", c)]
    if not month_cols:
        raise ValueError("Forecasting file must contain monthly columns like ith_01 to ith_24.")

    keep_cols = ["ith_part", "ith_loc"] + month_cols
    raw = raw[keep_cols].copy()

    raw["ith_part"] = normalize_part_number(raw["ith_part"])
    raw["ith_loc"] = raw["ith_loc"].astype(str).str.strip().str.upper()

    raw = raw[
        (raw["ith_part"] != "") &
        (raw["ith_part"].str.lower() != "nan")
    ].copy()

    for col in month_cols:
        raw[col] = pd.to_numeric(raw[col], errors="coerce").fillna(0)

    grouped = raw.groupby(["ith_part", "ith_loc"], as_index=False)[month_cols].sum()

    newest_first = get_forecast_month_columns_newest_first(month_cols)
    recent_6_cols = newest_first[:6]

    grouped["Forecast_Monthly_Avg"] = grouped[recent_6_cols].mean(axis=1)
    grouped["Forecast_6m_Avg"] = grouped["Forecast_Monthly_Avg"]

    grouped = grouped.rename(columns={
        "ith_part": "Match_Part",
        "ith_loc": "Forecast_Loc",
    })

    return grouped


def build_bunnings_woh_estimate(
    bunnings_df: pd.DataFrame,
    forecast_loc_df: pd.DataFrame | None
) -> pd.DataFrame:
    """
    Calculate Bunnings weeks on hand and recommended order quantities.
    """
    result = bunnings_df.copy()

    if forecast_loc_df is not None:
        result = result.merge(
            forecast_loc_df[["Match_Part", "Forecast_Loc", "Forecast_Monthly_Avg"]],
            on=["Match_Part", "Forecast_Loc"],
            how="left"
        )
    else:
        result["Forecast_Monthly_Avg"] = np.nan

    result["Forecast_Monthly_Avg"] = pd.to_numeric(result["Forecast_Monthly_Avg"], errors="coerce")
    result["Fallback_Monthly_Usage"] = pd.to_numeric(result["CY24_Sales"], errors="coerce").fillna(0) / 12.0

    result["Monthly_Usage_Used"] = np.where(
        result["Forecast_Monthly_Avg"].fillna(0) > 0,
        result["Forecast_Monthly_Avg"],
        result["Fallback_Monthly_Usage"]
    )

    result["Usage_Source"] = np.where(
        result["Forecast_Monthly_Avg"].fillna(0) > 0,
        "Forecast",
        "CY24 Sales Fallback"
    )

    result["Weekly_Usage"] = result["Monthly_Usage_Used"] / 4.33

    result["Weeks_on_Hand"] = np.where(
        result["Weekly_Usage"] > 0,
        result["SOH_Steelfort"] / result["Weekly_Usage"],
        np.nan
    )

    result["Recommended_Order_4W"] = np.where(
        result["Weekly_Usage"] > 0,
        np.ceil(((result["Weekly_Usage"] * 4) - result["SOH_Steelfort"]).clip(lower=0)),
        0
    )

    result["Bunnings_Status"] = "🟢 OK"
    result.loc[result["Weeks_on_Hand"].fillna(999999) < 4, "Bunnings_Status"] = "🟠 RISK"
    result.loc[result["Weeks_on_Hand"].fillna(999999) < 1, "Bunnings_Status"] = "🔴 URGENT"

    result["Weekly_Usage"] = result["Weekly_Usage"].round(2)
    result["Weeks_on_Hand"] = result["Weeks_on_Hand"].round(2)
    result["Monthly_Usage_Used"] = result["Monthly_Usage_Used"].round(2)
    result["Forecast_Monthly_Avg"] = result["Forecast_Monthly_Avg"].round(2)
    result["Fallback_Monthly_Usage"] = result["Fallback_Monthly_Usage"].round(2)
    result["Recommended_Order_4W"] = pd.to_numeric(
        result["Recommended_Order_4W"], errors="coerce"
    ).fillna(0).astype(int)

    return result