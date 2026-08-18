import numpy as np
import pandas as pd
import streamlit as st

from services.backorder_service import load_backorder_report_cached
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

# Same NetSuite saved-search shapes as Spare Parts Ordering (item export,
# 24-month forecast history, Custom Inventory Back Order Report) - just
# scoped to whole units (mowers, chippers, etc.) instead of spare parts, so
# this mode reuses the same loading/cleaning services rather than
# duplicating them.


def _apply_backorder_adjustment(df: pd.DataFrame, backorder_df: pd.DataFrame) -> pd.DataFrame:
    """
    Merge the back order report in and recompute Net After POs / Available
    Now / base Priority off an "effective committed" figure that's the
    larger of NetSuite's Committed field and real open-Sales-Order Back
    Ordered qty - mirrors the same adjustment Spare Parts Ordering makes,
    since Committed is often left sparse/empty in NetSuite.
    """
    df = df.merge(backorder_df, on="Part_Number", how="left")
    df["Back Ordered"] = df["Back Ordered"].fillna(0)
    df["Backorder Customers"] = df["Backorder Customers"].fillna(0).astype(int)

    effective_committed = df[["Qty Allocated", "Back Ordered"]].max(axis=1)
    df["Available Now"] = df["Qty on hand"] - effective_committed
    df["Net After POs"] = df["Qty on hand"] + df["Qty on Order"] - effective_committed

    df["Below Min?"] = df["Net After POs"] < df["Min"]
    df["Priority"] = "OK"
    df.loc[(df["Min"] > 0) & (df["Below Min?"]), "Priority"] = "Review"
    # Gate "High"/"Urgent" off effective_committed (Allocated OR Back
    # Ordered), not raw Qty Allocated alone - NetSuite's Committed field is
    # often left empty for units (confirmed against real data: every
    # backordered unit in the sample had Qty Allocated == 0), so gating on
    # Qty Allocated alone meant a part could be genuinely out of stock with
    # real customers waiting and still never surface as Urgent.
    df.loc[(df["Min"] > 0) & (df["Below Min?"]) & (effective_committed > 0), "Priority"] = "High"
    df.loc[(effective_committed > 0) & (df["Net After POs"] <= 0), "Priority"] = "Urgent"

    return df


def _filter_common(
    df: pd.DataFrame,
    priority_col: str,
    supplier_values: list,
    location_values: list,
    selected_priorities: list,
    text_search: str,
    only_need_order: bool,
    recommended_order_col: str,
    part_group_values: list | None = None,
    part_type_values: list | None = None,
) -> pd.DataFrame:
    filtered = df.copy()

    if supplier_values:
        filtered = filtered[filtered["POREF_SUPP"].isin(supplier_values)]

    if location_values:
        filtered = filtered[filtered["Loc"].astype(str).str.strip().isin(location_values)]

    if part_group_values and "Part Group" in filtered.columns:
        filtered = filtered[filtered["Part Group"].astype(str).str.strip().isin(part_group_values)]

    if part_type_values and "Part Type" in filtered.columns:
        filtered = filtered[filtered["Part Type"].astype(str).str.strip().isin(part_type_values)]

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


def _distinct_values(df: pd.DataFrame, column: str) -> list[str]:
    """
    Sorted non-blank distinct values for a column, or [] if it isn't present.

    Column-name based (not positional), so it's unaffected by NetSuite
    saved searches reordering their exported columns.
    """
    if column not in df.columns:
        return []

    return sorted(
        {str(x).strip() for x in df[column].dropna().tolist() if str(x).strip() != ""}
    )


def _render_grouping_filters(df: pd.DataFrame, key_prefix: str) -> tuple[list, list]:
    """
    Render the Part Group / Part Type multiselects side by side and return
    the selections.

    "Part Group" (e.g. "L8 - LM 480 SERIES") and "Part Type" (e.g.
    "LM - LM ROTARY ALLOY") are two separate NetSuite fields - Part Type
    was added to the saved search alongside the existing Part Group, so
    both are offered here.
    """
    group_col, type_col = st.columns(2)

    part_group_values = _distinct_values(df, "Part Group")
    selected_part_groups = group_col.multiselect(
        "Part Group",
        part_group_values,
        key=f"{key_prefix}_part_group",
        disabled=not part_group_values,
    )

    part_type_values = _distinct_values(df, "Part Type")
    selected_part_types = type_col.multiselect(
        "Part Type",
        part_type_values,
        key=f"{key_prefix}_part_type",
        help="NetSuite product grouping (e.g. LM - LM ROTARY ALLOY, LP - LM PARTS).",
        disabled=not part_type_values,
    )

    return selected_part_groups, selected_part_types


def _render_demand_forecast_tab(
    df: pd.DataFrame,
    forecast_loaded: bool,
    month_cols: list[str],
    backorder_loaded: bool,
) -> None:
    st.caption(
        "Projects monthly demand from the uploaded forecasting dataset and compares it "
        "against stock on hand / on order to suggest what to order and how much."
    )

    if not forecast_loaded:
        st.info(
            "Upload the Units forecasting dataset (ith_01 - ith_24 monthly history) above "
            "to enable demand-based ordering."
        )
        return

    settings_col1, settings_col2, settings_col3 = st.columns(3)

    demand_basis = settings_col1.selectbox(
        "Demand Basis",
        ["Forecast_Weighted_6m", "Custom Forecast Average"],
        key="units_demand_basis",
    )
    months_target = settings_col2.number_input(
        "Months Target", min_value=1, value=6, key="units_months_target"
    )
    only_need_order = settings_col3.checkbox(
        "Only items needing order", value=True, key="units_forecast_only_need_order"
    )

    custom_forecast_months = 3
    if demand_basis == "Custom Forecast Average":
        custom_forecast_months = st.slider(
            "Forecast Months",
            min_value=1,
            max_value=24,
            value=3,
            step=1,
            key="units_forecast_months",
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

    col1, col2, col3 = st.columns([2, 2, 2])

    supplier_values = sorted(
        [x for x in calc_df["POREF_SUPP"].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_suppliers = col1.multiselect("Supplier", supplier_values, key="units_forecast_supplier")

    location_values = sorted(
        [str(x).strip() for x in calc_df["Loc"].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_locations = col2.multiselect("Location", location_values, key="units_forecast_location")

    priorities = sorted(calc_df["Priority V2"].dropna().unique().tolist())
    selected_priorities = col3.multiselect(
        "Priority", priorities, default=priorities, key="units_forecast_priority"
    )

    selected_part_groups, selected_part_types = _render_grouping_filters(
        calc_df, key_prefix="units_forecast"
    )

    text_search = st.text_input(
        "Search part number or description", key="units_forecast_search"
    )

    filtered = _filter_common(
        calc_df,
        priority_col="Priority V2",
        supplier_values=selected_suppliers,
        location_values=selected_locations,
        selected_priorities=selected_priorities,
        text_search=text_search,
        only_need_order=only_need_order,
        recommended_order_col="Recommended Order",
        part_group_values=selected_part_groups,
        part_type_values=selected_part_types,
    )

    sort_options = [
        "Recommended Order", "Target Stock", "Demand_Per_Month_Used",
        "Net After POs", "Qty on hand", "Qty on Order", "Qty Allocated",
    ]
    if backorder_loaded:
        sort_options.insert(0, "Back Ordered")
    sort_options = [c for c in sort_options if c in filtered.columns]

    sort_col = st.selectbox("Sort by", sort_options, index=0, key="units_forecast_sort")
    sort_desc = st.toggle("Descending sort", value=True, key="units_forecast_sort_desc")

    if not filtered.empty:
        filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Rows shown", f"{len(filtered):,}")
    k2.metric(
        "Units to Order",
        f"{int(filtered['Recommended Order'].sum()) if not filtered.empty else 0:,}",
    )
    k3.metric(
        "Urgent items",
        f"{int((filtered['Priority V2'] == '🔴 URGENT').sum()) if not filtered.empty else 0:,}",
    )
    if backorder_loaded:
        k4.metric(
            "Units backordered",
            f"{int(filtered['Back Ordered'].sum()) if not filtered.empty else 0:,}",
        )
    else:
        k4.metric("Forecast Matched", f"{int(filtered['Forecast Matched?'].sum()) if not filtered.empty else 0:,}")

    display_columns = [
        "Part_Number", "Description", "POREF_SUPP", "Type", "Part Group", "Part Type", "Loc",
        "Qty on hand", "Qty Allocated", "Qty on Order", "Available Now", "Net After POs",
        "Demand_Per_Month_Used", "Target Stock", "Recommended Order",
        "Back Ordered", "Backorder Customers", "Priority V2",
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
        "⬇️ Download Units Demand Forecast Order CSV",
        data=csv_bytes,
        file_name="units_demand_forecast_order_list.csv",
        mime="text/csv",
        key="units_forecast_download",
    )

    st.divider()
    render_ai_insights(filtered.rename(columns={"Priority": "Priority V2"}), key_prefix="units_forecast_")


def _render_reorder_point_tab(df: pd.DataFrame, backorder_loaded: bool) -> None:
    st.caption(
        "Mirrors the Reorder Point / Preferred Stock Level logic from Spare Parts Ordering - "
        "no forecast needed. Orders when net stock drops below the Reorder Point, up to the "
        "Preferred Stock Level."
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

    only_need_order = st.checkbox(
        "Only items needing order", value=True, key="units_reorder_only_need_order"
    )

    col1, col2, col3 = st.columns([2, 2, 2])

    supplier_values = sorted(
        [x for x in calc_df["POREF_SUPP"].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_suppliers = col1.multiselect("Supplier", supplier_values, key="units_reorder_supplier")

    location_values = sorted(
        [str(x).strip() for x in calc_df["Loc"].dropna().unique().tolist() if str(x).strip() != ""]
    )
    selected_locations = col2.multiselect("Location", location_values, key="units_reorder_location")

    priorities = sorted(calc_df["Priority Display"].dropna().unique().tolist())
    selected_priorities = col3.multiselect(
        "Priority", priorities, default=priorities, key="units_reorder_priority"
    )

    selected_part_groups, selected_part_types = _render_grouping_filters(
        calc_df, key_prefix="units_reorder"
    )

    text_search = st.text_input(
        "Search part number or description", key="units_reorder_search"
    )

    filtered = _filter_common(
        calc_df,
        priority_col="Priority Display",
        supplier_values=selected_suppliers,
        location_values=selected_locations,
        selected_priorities=selected_priorities,
        text_search=text_search,
        only_need_order=only_need_order,
        recommended_order_col="Recommended Order",
        part_group_values=selected_part_groups,
        part_type_values=selected_part_types,
    )

    sort_options = [
        "Recommended Order", "Net After POs", "Shortage to Min", "Shortage to Max",
        "Qty on hand", "Qty on Order", "Qty Allocated", "Min", "Max",
    ]
    if backorder_loaded:
        sort_options.insert(0, "Back Ordered")
    sort_options = [c for c in sort_options if c in filtered.columns]

    sort_col = st.selectbox("Sort by", sort_options, index=0, key="units_reorder_sort")
    sort_desc = st.toggle("Descending sort", value=True, key="units_reorder_sort_desc")

    if not filtered.empty:
        filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("Rows shown", f"{len(filtered):,}")
    k2.metric(
        "Units to Order",
        f"{int(filtered['Recommended Order'].sum()) if not filtered.empty else 0:,}",
    )
    k3.metric(
        "Urgent items",
        f"{int((filtered['Priority Display'] == '🔴 URGENT').sum()) if not filtered.empty else 0:,}",
    )
    if backorder_loaded:
        k4.metric(
            "Units backordered",
            f"{int(filtered['Back Ordered'].sum()) if not filtered.empty else 0:,}",
        )
    else:
        k4.metric(
            "Below Reorder Point",
            f"{int(filtered['Below Min?'].sum()) if not filtered.empty else 0:,}",
        )

    display_columns = [
        "Part_Number", "Description", "POREF_SUPP", "Type", "Part Group", "Part Type", "Loc",
        "Qty on hand", "Qty Allocated", "Qty on Order", "Net After POs",
        "Min", "Max", "Recommended Order",
        "Back Ordered", "Backorder Customers", "Priority Display",
    ]
    display_columns = [c for c in display_columns if c in filtered.columns]
    filtered_display = filtered[display_columns].rename(
        columns={"Min": "Reorder Point", "Max": "Preferred Stock Level", "Priority Display": "Priority"}
    )

    st.dataframe(filtered_display, width="stretch", hide_index=True)

    csv_bytes = filtered_display.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download Units Reorder Point Order CSV",
        data=csv_bytes,
        file_name="units_reorder_point_order_list.csv",
        mime="text/csv",
        key="units_reorder_download",
    )

    st.divider()
    render_ai_insights(filtered.rename(columns={"Priority Display": "Priority V2"}), key_prefix="units_reorder_")


def render_units_mode() -> None:
    """
    Render the Units Ordering workflow - whole units (mowers, chippers,
    etc.) rather than spare parts, using the same NetSuite saved-search
    shapes as Spare Parts Ordering.

    Files are uploaded once at the top and shared by both tabs:
    - Demand Forecast Ordering (default): projects monthly demand from the
      forecasting dataset and compares it to stock on hand/on order.
    - Reorder Point Ordering: the same Reorder Point / Preferred Stock
      Level logic as Spare Parts Ordering, no forecast required.
    """
    st.subheader("Units Ordering")
    st.caption(
        "Upload the Units-scoped NetSuite exports below - both tabs share these files."
    )

    units_inventory_file = st.file_uploader(
        "Upload NetSuite Units Inventory CSV or Excel export "
        "(e.g. Units Report / Units Reorder Report - scoped to whole units, not spare parts)",
        type=["csv", "xlsx", "xls"],
        key="units_inventory_file",
    )

    units_forecast_file = st.file_uploader(
        "Upload Units Forecasting Dataset CSV or Excel (ith_01 - ith_24 monthly history - "
        "drives the Demand Forecast Ordering tab)",
        type=["csv", "xlsx", "xls"],
        key="units_forecast_file",
    )

    units_backorder_file = st.file_uploader(
        "Upload NetSuite Custom Inventory Back Order Report (optional - drives Urgent "
        "priority directly from real open Sales Order shortfalls)",
        type=["csv"],
        key="units_backorder_file",
    )

    if not units_inventory_file:
        st.info("Upload your NetSuite Units inventory export to get started.")
        return

    inventory_bytes, inventory_name = get_uploaded_file_bytes(units_inventory_file)

    with st.spinner("Loading units inventory file..."):
        df = clean_inventory_data_cached(inventory_bytes, inventory_name)

    forecast_loaded = False
    month_cols: list[str] = []

    if units_forecast_file is not None:
        forecast_bytes, forecast_name = get_uploaded_file_bytes(units_forecast_file)
        with st.spinner("Loading units forecasting file..."):
            forecast_df, forecast_detail, month_cols = load_forecast_history_cached(
                forecast_bytes, forecast_name,
            )
        df = merge_inventory_and_forecast(df, forecast_df)
        df["Forecast Matched?"] = df["Forecast_3m_Avg"].notna() if "Forecast_3m_Avg" in df.columns else False
        forecast_loaded = True

    if not month_cols:
        month_cols = get_forecast_month_columns_newest_first(df.columns)

    backorder_loaded = False

    if units_backorder_file is not None:
        backorder_bytes, backorder_name = get_uploaded_file_bytes(units_backorder_file)
        with st.spinner("Loading units back order report..."):
            backorder_df = load_backorder_report_cached(backorder_bytes, backorder_name)
        df = _apply_backorder_adjustment(df, backorder_df)
        backorder_loaded = True
    else:
        df["Back Ordered"] = 0
        df["Backorder Customers"] = 0

    tab1, tab2 = st.tabs(["📈 Demand Forecast Ordering", "🎯 Reorder Point Ordering"])

    with tab1:
        _render_demand_forecast_tab(df, forecast_loaded, month_cols, backorder_loaded)

    with tab2:
        _render_reorder_point_tab(df, backorder_loaded)

    with st.expander("Detected file structure"):
        st.write("Units Forecast File Loaded:", forecast_loaded)
        st.write("Units Back Order Report Loaded:", backorder_loaded)
        st.write("Units Inventory Rows Loaded:", len(df))
        st.write("Units Inventory Columns Found:")
        st.write(list(df.columns))
