import numpy as np
import pandas as pd
import streamlit as st

from services.forecast_service import (
    load_forecast_history_cached,
    merge_inventory_and_forecast,
)
from services.inventory_service import (
    apply_inventory_calculations,
    clean_inventory_data_cached,
)
from ui.ai_insights_view import render_ai_insights
from utils.helpers import get_forecast_month_columns_newest_first, get_uploaded_file_bytes

# Same NetSuite item-export shape as Units/Spare Parts (Part_Number, Supplier,
# Location On Hand, Reorder Point, Preferred Stock Level, etc.), scoped to
# Cutting Edge / POC parts - so this mode reuses the same loading/cleaning
# services rather than duplicating them.

CATEGORY_MANUFACTURE = "🔧 Manufacture"
CATEGORY_PURCHASE = "🛒 Purchase"


def _classify_category(df: pd.DataFrame) -> pd.DataFrame:
    """
    The core POC Check rule: no supplier on the part means it's built
    in-house (Manufacture), a supplier present means it's bought in
    (Purchase).
    """
    df = df.copy()
    has_supplier = df["POREF_SUPP"].astype(str).str.strip() != ""
    df["Category"] = np.where(has_supplier, CATEGORY_PURCHASE, CATEGORY_MANUFACTURE)
    return df


def _filter_common(
    df: pd.DataFrame,
    priority_col: str,
    category_values: list,
    supplier_values: list,
    location_values: list,
    selected_priorities: list,
    text_search: str,
    only_need_order: bool,
    recommended_order_col: str,
    part_group_values: list | None = None,
) -> pd.DataFrame:
    filtered = df.copy()

    if category_values:
        filtered = filtered[filtered["Category"].isin(category_values)]

    if supplier_values:
        filtered = filtered[filtered["POREF_SUPP"].isin(supplier_values)]

    if location_values:
        filtered = filtered[filtered["Loc"].astype(str).str.strip().isin(location_values)]

    if part_group_values and "Part Group" in filtered.columns:
        filtered = filtered[filtered["Part Group"].astype(str).str.strip().isin(part_group_values)]

    if selected_priorities:
        filtered = filtered[filtered[priority_col].isin(selected_priorities)]

    if text_search:
        q = text_search.strip().lower()
        filtered = filtered[
            filtered["Part_Number"].astype(str).str.lower().str.contains(q, na=False)
            | filtered["Description"].astype(str).str.lower().str.contains(q, na=False)
        ]

    if only_need_order:
        filtered = filtered[filtered[recommended_order_col] > 0]

    return filtered


def _category_metrics(filtered: pd.DataFrame, recommended_order_col: str):
    k1, k2, k3, k4, k5 = st.columns(5)
    k1.metric("Rows shown", f"{len(filtered):,}")

    if not filtered.empty:
        manufacture_units = int(
            filtered.loc[filtered["Category"] == CATEGORY_MANUFACTURE, recommended_order_col].sum()
        )
        purchase_units = int(
            filtered.loc[filtered["Category"] == CATEGORY_PURCHASE, recommended_order_col].sum()
        )
    else:
        manufacture_units = 0
        purchase_units = 0

    k2.metric("Units to Manufacture", f"{manufacture_units:,}")
    k3.metric("Units to Purchase", f"{purchase_units:,}")
    return k4, k5


def _render_demand_forecast_tab(
    df: pd.DataFrame,
    forecast_loaded: bool,
    month_cols: list[str],
    backordered_loaded: bool,
) -> None:
    st.caption(
        "Projects monthly demand from the uploaded forecasting dataset and compares it "
        "against stock on hand / on order to work out how much of each POC item to build "
        "or buy."
    )

    if not forecast_loaded:
        st.info(
            "Upload the POC forecasting dataset (ith_01 - ith_24 monthly history) above "
            "to enable demand-based ordering."
        )
        return

    settings_col1, settings_col2, settings_col3 = st.columns(3)

    demand_basis = settings_col1.selectbox(
        "Demand Basis",
        ["Forecast_Weighted_6m", "Custom Forecast Average"],
        key="poc_demand_basis",
    )
    months_target = settings_col2.number_input(
        "Months Target", min_value=1, value=6, key="poc_months_target"
    )
    only_need_order = settings_col3.checkbox(
        "Only items needing order", value=True, key="poc_forecast_only_need_order"
    )

    custom_forecast_months = 3
    if demand_basis == "Custom Forecast Average":
        custom_forecast_months = st.slider(
            "Forecast Months",
            min_value=1,
            max_value=24,
            value=3,
            step=1,
            key="poc_forecast_months",
            help="Average forecast demand using the most recent selected number of months.",
        )

    calc_df = apply_inventory_calculations(
        df=df,
        demand_basis=demand_basis,
        months_target=months_target,
        use_eoq_rounding=False,
        forecast_loaded=True,
        custom_forecast_months=custom_forecast_months,
        month_cols=month_cols,
    )

    # A backordered POC item is urgent regardless of what the stock/demand
    # math says - real customers are waiting on it.
    calc_df.loc[calc_df["Back Ordered"] > 0, "Priority V2"] = "🔴 URGENT"

    col1, col2, col3, col4 = st.columns([1.5, 2, 2, 2])

    category_values = [CATEGORY_MANUFACTURE, CATEGORY_PURCHASE]
    selected_categories = col1.multiselect(
        "Category", category_values, default=category_values, key="poc_forecast_category"
    )

    supplier_values = sorted(
        [x for x in calc_df["POREF_SUPP"].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_suppliers = col2.multiselect("Supplier", supplier_values, key="poc_forecast_supplier")

    location_values = sorted(
        [str(x).strip() for x in calc_df["Loc"].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_locations = col3.multiselect("Location", location_values, key="poc_forecast_location")

    priorities = sorted(calc_df["Priority V2"].dropna().unique().tolist())
    selected_priorities = col4.multiselect(
        "Priority", priorities, default=priorities, key="poc_forecast_priority"
    )

    selected_part_groups = []
    if "Part Group" in calc_df.columns:
        part_group_values = sorted(
            [x for x in calc_df["Part Group"].dropna().unique().tolist() if str(x).strip() != ""]
        )
        selected_part_groups = st.multiselect(
            "Part Group", part_group_values, key="poc_forecast_part_group"
        )

    text_search = st.text_input(
        "Search part number or description", key="poc_forecast_search"
    )

    filtered = _filter_common(
        calc_df,
        priority_col="Priority V2",
        category_values=selected_categories,
        supplier_values=selected_suppliers,
        location_values=selected_locations,
        selected_priorities=selected_priorities,
        text_search=text_search,
        only_need_order=only_need_order,
        recommended_order_col="Recommended Order",
        part_group_values=selected_part_groups,
    )

    sort_options = [
        "Back Ordered", "Recommended Order", "Target Stock", "Demand_Per_Month_Used",
        "Net After POs", "Qty on hand", "Qty on Order", "Qty Allocated",
    ]
    sort_options = [c for c in sort_options if c in filtered.columns]

    sort_col = st.selectbox("Sort by", sort_options, index=0, key="poc_forecast_sort")
    sort_desc = st.toggle("Descending sort", value=True, key="poc_forecast_sort_desc")

    if not filtered.empty:
        filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    k4, k5 = _category_metrics(filtered, "Recommended Order")
    k4.metric(
        "Urgent items",
        f"{int((filtered['Priority V2'] == '🔴 URGENT').sum()) if not filtered.empty else 0:,}",
    )
    if backordered_loaded:
        k5.metric(
            "Items backordered",
            f"{int((filtered['Back Ordered'] > 0).sum()) if not filtered.empty else 0:,}",
        )

    display_columns = [
        "Part_Number", "Description", "Category", "POREF_SUPP", "Type", "Part Group", "Loc",
        "Qty on hand", "Qty Allocated", "Qty on Order", "Available Now", "Net After POs",
        "Demand_Per_Month_Used", "Target Stock", "Recommended Order", "Back Ordered", "Priority V2",
    ]
    display_columns = [c for c in display_columns if c in filtered.columns]
    # Drop the base Reorder-Point-only "Priority" column (from
    # clean_inventory_data_cached) before renaming "Priority V2" to
    # "Priority" for display - otherwise the two collide into a duplicate
    # column, which pyarrow/st.dataframe can't serialise.
    filtered = filtered.drop(columns=["Priority"], errors="ignore")
    filtered = filtered.rename(columns={"Priority V2": "Priority"})
    display_columns = [c if c != "Priority V2" else "Priority" for c in display_columns]

    st.dataframe(filtered[display_columns], width="stretch", hide_index=True)

    csv_bytes = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download POC Demand Forecast Check CSV",
        data=csv_bytes,
        file_name="poc_demand_forecast_check.csv",
        mime="text/csv",
        key="poc_forecast_download",
    )

    st.divider()
    render_ai_insights(filtered.rename(columns={"Priority": "Priority V2"}), key_prefix="poc_forecast_")


def _render_reorder_point_tab(df: pd.DataFrame, backordered_loaded: bool) -> None:
    st.caption(
        "Mirrors the Reorder Point / Preferred Stock Level logic from Spare Parts / Units "
        "Ordering - no forecast needed. Orders/builds when net stock drops below the "
        "Reorder Point, up to the Preferred Stock Level."
    )

    calc_df = df.copy()

    calc_df["Recommended Order"] = np.where(
        calc_df["Below Min?"],
        np.ceil((calc_df["Max"] - calc_df["Net After POs"]).clip(lower=0)),
        0,
    )
    calc_df["Recommended Order"] = (
        pd.to_numeric(calc_df["Recommended Order"], errors="coerce").fillna(0).astype(int)
    )

    priority_display_map = {
        "OK": "🟢 OK",
        "Review": "🟡 REVIEW",
        "High": "🟠 HIGH",
        "Urgent": "🔴 URGENT",
    }
    calc_df["Priority Display"] = calc_df["Priority"].map(priority_display_map).fillna(calc_df["Priority"])

    # A backordered POC item is urgent regardless of what the Reorder
    # Point/Preferred Stock Level math says - real customers are waiting.
    calc_df.loc[calc_df["Back Ordered"] > 0, "Priority Display"] = "🔴 URGENT"

    only_need_order = st.checkbox(
        "Only items needing order", value=True, key="poc_reorder_only_need_order"
    )

    col1, col2, col3, col4 = st.columns([1.5, 2, 2, 2])

    category_values = [CATEGORY_MANUFACTURE, CATEGORY_PURCHASE]
    selected_categories = col1.multiselect(
        "Category", category_values, default=category_values, key="poc_reorder_category"
    )

    supplier_values = sorted(
        [x for x in calc_df["POREF_SUPP"].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_suppliers = col2.multiselect("Supplier", supplier_values, key="poc_reorder_supplier")

    location_values = sorted(
        [str(x).strip() for x in calc_df["Loc"].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_locations = col3.multiselect("Location", location_values, key="poc_reorder_location")

    priorities = sorted(calc_df["Priority Display"].dropna().unique().tolist())
    selected_priorities = col4.multiselect(
        "Priority", priorities, default=priorities, key="poc_reorder_priority"
    )

    selected_part_groups = []
    if "Part Group" in calc_df.columns:
        part_group_values = sorted(
            [x for x in calc_df["Part Group"].dropna().unique().tolist() if str(x).strip() != ""]
        )
        selected_part_groups = st.multiselect(
            "Part Group", part_group_values, key="poc_reorder_part_group"
        )

    text_search = st.text_input(
        "Search part number or description", key="poc_reorder_search"
    )

    filtered = _filter_common(
        calc_df,
        priority_col="Priority Display",
        category_values=selected_categories,
        supplier_values=selected_suppliers,
        location_values=selected_locations,
        selected_priorities=selected_priorities,
        text_search=text_search,
        only_need_order=only_need_order,
        recommended_order_col="Recommended Order",
        part_group_values=selected_part_groups,
    )

    sort_options = [
        "Back Ordered", "Recommended Order", "Net After POs", "Shortage to Min", "Shortage to Max",
        "Qty on hand", "Qty on Order", "Qty Allocated", "Min", "Max",
    ]
    sort_options = [c for c in sort_options if c in filtered.columns]

    sort_col = st.selectbox("Sort by", sort_options, index=0, key="poc_reorder_sort")
    sort_desc = st.toggle("Descending sort", value=True, key="poc_reorder_sort_desc")

    if not filtered.empty:
        filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    k4, k5 = _category_metrics(filtered, "Recommended Order")
    k4.metric(
        "Urgent items",
        f"{int((filtered['Priority Display'] == '🔴 URGENT').sum()) if not filtered.empty else 0:,}",
    )
    if backordered_loaded:
        k5.metric(
            "Items backordered",
            f"{int((filtered['Back Ordered'] > 0).sum()) if not filtered.empty else 0:,}",
        )

    display_columns = [
        "Part_Number", "Description", "Category", "POREF_SUPP", "Type", "Part Group", "Loc",
        "Qty on hand", "Qty Allocated", "Qty on Order", "Net After POs",
        "Min", "Max", "Recommended Order", "Back Ordered", "Priority Display",
    ]
    display_columns = [c for c in display_columns if c in filtered.columns]
    filtered_display = filtered[display_columns].rename(
        columns={"Min": "Reorder Point", "Max": "Preferred Stock Level", "Priority Display": "Priority"}
    )

    st.dataframe(filtered_display, width="stretch", hide_index=True)

    csv_bytes = filtered_display.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download POC Reorder Point Check CSV",
        data=csv_bytes,
        file_name="poc_reorder_point_check.csv",
        mime="text/csv",
        key="poc_reorder_download",
    )

    st.divider()
    render_ai_insights(filtered.rename(columns={"Priority Display": "Priority V2"}), key_prefix="poc_reorder_")


def render_poc_check_mode() -> None:
    """
    Render the POC Check workflow - Cutting Edge (POC) items, split into
    what needs to be Manufactured (no supplier on the part) vs Purchased
    (a supplier is present), using the same NetSuite saved-search shapes as
    Spare Parts / Units Ordering.

    Files are uploaded once at the top and shared by both tabs:
    - Demand Forecast Check (default): projects monthly demand from the
      forecasting dataset and compares it to stock on hand/on order.
    - Reorder Point Check: the same Reorder Point / Preferred Stock Level
      logic as Spare Parts / Units Ordering, no forecast required.
    """
    st.subheader("POC Check")
    st.caption(
        "Upload the POC-scoped NetSuite exports below - both tabs share these files. "
        "Items with no Supplier are flagged to Manufacture; items with a Supplier are "
        "flagged to Purchase."
    )

    poc_inventory_file = st.file_uploader(
        "Upload NetSuite POC Inventory CSV or Excel export "
        "(e.g. POC Check - scoped to Cutting Edge / POC parts)",
        type=["csv", "xlsx", "xls"],
        key="poc_inventory_file",
    )

    poc_forecast_file = st.file_uploader(
        "Upload POC Forecasting Dataset CSV or Excel (ith_01 - ith_24 monthly history - "
        "drives the Demand Forecast Check tab)",
        type=["csv", "xlsx", "xls"],
        key="poc_forecast_file",
    )

    if not poc_inventory_file:
        st.info("Upload your NetSuite POC inventory export to get started.")
        return

    inventory_bytes, inventory_name = get_uploaded_file_bytes(poc_inventory_file)

    with st.spinner("Loading POC inventory file..."):
        raw_df = clean_inventory_data_cached(inventory_bytes, inventory_name)

    total_rows = len(raw_df)
    part_series = raw_df["Part_Number"].astype(str).str.upper()
    df = raw_df[part_series.str.startswith("POC")].copy()
    non_poc_rows = total_rows - len(df)

    if df.empty:
        st.warning(
            "No POC-prefixed part numbers found in this file - double-check you uploaded "
            "the right export."
        )
        return

    df = _classify_category(df)

    # The POC export can carry its own flat "Back Ordered" column directly
    # on each row (unlike Units/Spare Parts, which get backorders from a
    # separate grouped NetSuite report) - coerce it to numeric so both tabs
    # can force Urgent priority off it. Defaults to 0 for older exports
    # that don't have the column yet.
    df["Back Ordered"] = pd.to_numeric(df.get("Back Ordered", 0), errors="coerce").fillna(0)
    backordered_loaded = bool((df["Back Ordered"] > 0).any())

    forecast_loaded = False
    month_cols: list[str] = []

    if poc_forecast_file is not None:
        forecast_bytes, forecast_name = get_uploaded_file_bytes(poc_forecast_file)
        with st.spinner("Loading POC forecasting file..."):
            forecast_df, forecast_detail, month_cols = load_forecast_history_cached(
                forecast_bytes, forecast_name,
            )
        df = merge_inventory_and_forecast(df, forecast_df)
        df["Forecast Matched?"] = df["Forecast_3m_Avg"].notna() if "Forecast_3m_Avg" in df.columns else False
        forecast_loaded = True

    if not month_cols:
        month_cols = get_forecast_month_columns_newest_first(df.columns)

    tab1, tab2 = st.tabs(["📈 Demand Forecast Check", "🎯 Reorder Point Check"])

    with tab1:
        _render_demand_forecast_tab(df, forecast_loaded, month_cols, backordered_loaded)

    with tab2:
        _render_reorder_point_tab(df, backordered_loaded)

    with st.expander("Detected file structure"):
        st.write("POC Forecast File Loaded:", forecast_loaded)
        st.write("Back Ordered Column Found:", backordered_loaded)
        st.write("Total Rows In Upload:", total_rows)
        st.write("Non-POC Rows Excluded:", non_poc_rows)
        st.write("POC Rows Loaded:", len(df))
        manufacture_count = int((df["Category"] == CATEGORY_MANUFACTURE).sum())
        purchase_count = int((df["Category"] == CATEGORY_PURCHASE).sum())
        st.write("Manufacture Items (no Supplier):", manufacture_count)
        st.write("Purchase Items (Supplier present):", purchase_count)
        st.write("POC Inventory Columns Found:")
        st.write(list(df.columns))
