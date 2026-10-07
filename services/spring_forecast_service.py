import pandas as pd
import streamlit as st

from services.inventory_service import clean_inventory_data_cached


def classify_spring_product_line(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add a practical spring-review category.

    Used as a filter column in the main inventory view rather than a
    separate worksheet mode, since it applies to the same NetSuite export.

    POX is intentionally separated first as Resources because these are
    used to support POC / Cutting Edge stock builds.
    """
    df = df.copy()
    part = df["Part_Number"].astype(str).str.upper()
    desc = df["Description"].astype(str).str.upper()

    df["Spring Category"] = "Other Power Parts"
    df.loc[part.str.startswith("POX"), "Spring Category"] = "Resources / POX"
    df.loc[(df["Spring Category"] != "Resources / POX") & (part.str.startswith("POC")), "Spring Category"] = "Cutting Edge / POC"

    spark_mask = (
        part.str.startswith(("PO132", "POC132", "PO131", "POC131")) |
        desc.str.contains("SPARK|CHAMPION|NGK|PLUG", regex=True, na=False)
    )
    df.loc[(df["Spring Category"] != "Resources / POX") & spark_mask, "Spring Category"] = "Spark Plugs"

    df.loc[(df["Spring Category"] != "Resources / POX") & desc.str.contains("AIR FILTER|PREFILTER|PRE-FILTER| FILTER|\\bAF\\b", regex=True, na=False), "Spring Category"] = "Air Filters"
    df.loc[(df["Spring Category"] != "Resources / POX") & desc.str.contains("BLADE|FLAIL|KNIFE", regex=True, na=False), "Spring Category"] = "Blades / Flails"
    df.loc[(df["Spring Category"] != "Resources / POX") & desc.str.contains("TRIM|TRIMMER|LINE|HEAD|LITTL JUEY|NYLON", regex=True, na=False), "Spring Category"] = "Trimmer Line / Heads"
    df.loc[(df["Spring Category"] != "Resources / POX") & desc.str.contains("CHAIN|CHAINSAW|CHAIN SAW|BAR|FILE|VALLORBE", regex=True, na=False), "Spring Category"] = "Chainsaw Files / Accessories"
    df.loc[(df["Spring Category"] != "Resources / POX") & desc.str.contains("ROPE|STARTER|HANDLE", regex=True, na=False), "Spring Category"] = "Starter Rope / Handles"

    return df


@st.cache_data(show_spinner=False)
def load_inventory_with_spring_cached(file_bytes: bytes, file_name: str) -> pd.DataFrame:
    """
    clean_inventory_data_cached + classify_spring_product_line as one cached
    step. The classifier's text searches over every row took ~0.6s of each
    click in Spare Parts Ordering when it ran outside the cache.
    """
    return classify_spring_product_line(clean_inventory_data_cached(file_bytes, file_name))
