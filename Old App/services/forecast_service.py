import re
import numpy as np
import pandas as pd
import streamlit as st

from services.file_loader import load_file_from_bytes
from utils.helpers import normalize_part_number, get_forecast_month_columns_newest_first


@st.cache_data(show_spinner=False)
def load_forecast_history_cached(file_bytes: bytes, file_name: str) -> tuple[pd.DataFrame, pd.DataFrame, list[str]]:
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

    detail = raw.rename(columns={"ith_part": "Part_Number"}).copy()
    detail["Part_Number"] = normalize_part_number(detail["Part_Number"])

    return grouped, detail, newest_first


@st.cache_data(show_spinner=False)
def merge_inventory_and_forecast(inventory_df: pd.DataFrame, forecast_df: pd.DataFrame | None) -> pd.DataFrame:
    """
    Merge inventory with forecast data.
    """
    if forecast_df is None:
        return inventory_df.copy()
    return inventory_df.merge(forecast_df, on="Part_Number", how="left")