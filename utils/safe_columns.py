import pandas as pd
import numpy as np


def safe_get_column(df: pd.DataFrame, col_name: str, default=0):
    """Safely get a column from dataframe, return default if missing."""
    if df is None or df.empty:
        return pd.Series([default] * len(df)) if df is not None else pd.Series([])
    return df.get(col_name, pd.Series([default] * len(df)))


def has_column(df: pd.DataFrame, col_name: str) -> bool:
    """Check if column exists in dataframe."""
    if df is None or df.empty:
        return False
    return col_name in df.columns


def get_numeric_column(df: pd.DataFrame, col_name: str, fill_value=0):
    """Get numeric column, converting to numeric and filling NaN."""
    if not has_column(df, col_name):
        return pd.Series([fill_value] * len(df))
    return pd.to_numeric(df[col_name], errors='coerce').fillna(fill_value)


def get_status_column(df: pd.DataFrame):
    """Get status column - handles multiple naming conventions."""
    for col in ["Status", "Bunnings_Status", "Status_Flag"]:
        if has_column(df, col):
            return df[col]
    return pd.Series([""] * len(df))


def get_location_column(df: pd.DataFrame):
    """Get location column - handles multiple naming conventions."""
    for col in ["Forecast_Loc", "Location", "Warehouse", "Site"]:
        if has_column(df, col):
            return df[col]
    return pd.Series([""] * len(df))


def get_category_column(df: pd.DataFrame):
    """Get category column - handles multiple naming conventions."""
    for col in ["Category", "Type", "Product_Type", "Family"]:
        if has_column(df, col):
            return df[col]
    return pd.Series([""] * len(df))
