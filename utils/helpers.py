import re
import numpy as np
import pandas as pd


def safe_int(value, default=0):
    """
    Safely convert a value to int.
    Returns default if conversion fails.
    """
    try:
        return int(value)
    except Exception:
        return default


def get_filter_config(worksheet_type: str) -> tuple[str, str]:
    """
    Decide which main filter column to use based on worksheet type.

    MTD sheets are filtered by Type.
    All other standard inventory sheets are filtered by Supplier.
    """
    if worksheet_type == "MTD":
        return "Type", "Type"
    return "POREF_SUPP", "Supplier"


def get_uploaded_file_bytes(uploaded_file):
    """
    Return raw bytes and original filename from a Streamlit uploaded file.
    """
    if uploaded_file is None:
        return None, None
    return uploaded_file.getvalue(), uploaded_file.name


def normalize_part_number(series: pd.Series) -> pd.Series:
    """
    Standardise part numbers so files merge more reliably.
    """
    return (
        series.astype(str)
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .str.replace(r"\.0$", "", regex=True)
        .str.upper()
    )


def format_numeric_display(value):
    """
    Make values cleaner for UI display.
    """
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
    Return forecast month columns in newest-to-oldest order.

    IMPORTANT:
    Forecast headers are reversed in the export:
    - ith_24 = most recent month
    - ith_01 = oldest month
    """
    month_cols = [c for c in columns if re.fullmatch(r"ith_\d{2}", str(c))]
    return sorted(month_cols, key=lambda x: int(str(x).split("_")[1]), reverse=True)