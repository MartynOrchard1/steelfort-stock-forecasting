import numpy as np
import pandas as pd
import streamlit as st

from services.forecast_service import (
    load_forecast_history_cached,
    merge_inventory_and_forecast,
)
from services.inventory_service import (
    apply_inventory_calculations,
    apply_inventory_filters,
    clean_inventory_data_cached,
)
from services.spring_forecast_service import classify_spring_product_line
from services.backorder_service import load_backorder_report_cached
from ui.dialogs import show_part_details_dialog
from utils.helpers import (
    get_forecast_month_columns_newest_first,
    get_uploaded_file_bytes,
)

from ui.demand_trend_preview import render_demand_trend_preview


def render_inventory_mode() -> None:
    """
    Render the main NetSuite-based inventory workflow.

    NetSuite gives one combined item export covering all parts and
    suppliers, so there's no worksheet-type branching here anymore - just
    upload the export, optionally attach the legacy forecast file, and
    filter/sort the resulting table. Spring seasonal categorisation is
    folded in as a filter rather than a separate mode.
    """
    inventory_file = st.file_uploader(
        "Upload NetSuite Inventory CSV or Excel export",
        type=["csv", "xlsx", "xls"],
        key="inventory_file",
    )

    forecast_file = st.file_uploader(
        "Upload Forecasting CSV or Excel (optional - legacy TIMS forecast history, "
        "since stock movement history wasn't carried over into NetSuite)",
        type=["csv", "xlsx", "xls"],
        key="forecast_file",
    )

    backorder_file = st.file_uploader(
        "Upload NetSuite Custom Inventory Back Order Report (optional - drives Urgent "
        "priority directly from real open Sales Order shortfalls)",
        type=["csv"],
        key="backorder_file",
    )

    if not inventory_file:
        st.info("Upload your NetSuite inventory export to get started.")
        return

    inventory_bytes, inventory_name = get_uploaded_file_bytes(inventory_file)

    with st.spinner("Loading inventory file..."):
        inventory_df = clean_inventory_data_cached(inventory_bytes, inventory_name)
        inventory_df = classify_spring_product_line(inventory_df)

    forecast_df = None
    forecast_detail = None
    forecast_loaded = False
    forecast_mode = "Static worksheet averages"
    month_cols = []

    if forecast_file is not None:
        forecast_bytes, forecast_name = get_uploaded_file_bytes(forecast_file)
        with st.spinner("Loading forecasting file..."):
            forecast_df, forecast_detail, month_cols = load_forecast_history_cached(
                forecast_bytes,
                forecast_name,
            )
        forecast_loaded = True
        forecast_mode = "Forecast dataset (legacy - not yet available in NetSuite)"

    with st.spinner("Preparing final dataset..."):
        df = merge_inventory_and_forecast(inventory_df, forecast_df)

    if forecast_loaded and "Forecast_3m_Avg" in df.columns:
        df["Forecast Matched?"] = df["Forecast_3m_Avg"].notna()
    else:
        df["Forecast Matched?"] = False

    main_filter_col, main_filter_label = "POREF_SUPP", "Supplier"

    st.sidebar.header("📦 Ordering Settings")

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
    hide_factory = st.sidebar.checkbox(
        "Hide Factory Numbers (whole units, not spare parts)", value=True,
        help='Part numbers starting with "F" or "X" - not something you purchase.',
    )
    hide_dewalt = st.sidebar.checkbox(
        "Hide DeWalt parts", value=True,
        help='Part numbers starting with "DT", "DC", or "MP".',
    )
    hide_miele = st.sidebar.checkbox(
        "Hide Miele parts", value=True,
        help='Part numbers starting with "PM".',
    )

    show_poxpb = st.sidebar.checkbox("Show ONLY POXPB parts", value=False)
    show_poc_only = st.sidebar.checkbox("Show ONLY POC parts", value=False)
    show_pox_only = st.sidebar.checkbox("Show ONLY POX parts", value=False)
    show_poxcc_only = st.sidebar.checkbox("Show ONLY POXCC parts", value=False)

    custom_forecast_months = 3

    if forecast_loaded:
        demand_basis = st.sidebar.selectbox(
            "Demand Basis",
            ["Custom Forecast Average", "Forecast_Weighted_6m", "6mAvg", "12mAvg"]
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

    backorder_loaded = False

    if backorder_file is not None:
        backorder_bytes, backorder_name = get_uploaded_file_bytes(backorder_file)
        with st.spinner("Loading back order report..."):
            backorder_df = load_backorder_report_cached(backorder_bytes, backorder_name)

        df = df.merge(backorder_df, on="Part_Number", how="left")
        df["Back Ordered"] = df["Back Ordered"].fillna(0)
        df["Backorder Customers"] = df["Backorder Customers"].fillna(0).astype(int)
        backorder_loaded = True

        # A backorder just means a Sales Order line hasn't shipped yet - it
        # doesn't automatically mean we're short of stock (e.g. 16 on hand,
        # 1 backordered is just a fulfillment/picking lag, not a shortage).
        # Treat Back Ordered as a more reliable stand-in for Committed
        # (which NetSuite often leaves sparse/empty) and only let it push
        # Net After POs - and therefore Priority - into Urgent when the
        # backorder actually can't be covered by what's on hand + on order.
        effective_committed = df[["Qty Allocated", "Back Ordered"]].max(axis=1)
        df["Available"] = df["Qty on hand"] - effective_committed
        df["Net After POs"] = df["Qty on hand"] + df["Qty on Order"] - effective_committed

        df["Priority V2"] = "🟢 OK"
        df.loc[df["Net After POs"] < 0, "Priority V2"] = "🔴 URGENT"
        df.loc[
            (df["Net After POs"] >= 0) & (df["Net After POs"] < df["Effective Min"]),
            "Priority V2"
        ] = "🟡 REPLENISH"

        # Recommended Order was calculated before we knew about the
        # backorder, off the old Net After POs - recompute it now so a
        # part that's genuinely short (like this one: Target 0, but -3
        # after the backorder) actually shows a quantity to order instead
        # of silently staying at 0 and disappearing from "needs order"
        # filters.
        df["Base Recommended Order"] = np.ceil(
            df["Target Stock"] - df["Net After POs"]
        ).clip(lower=0)

        if use_eoq_rounding:
            valid_eoq = df["EOQ"] > 1
            df["Base Recommended Order"] = np.where(
                valid_eoq & (df["Base Recommended Order"] > 0),
                np.ceil(df["Base Recommended Order"] / df["EOQ"]) * df["EOQ"],
                df["Base Recommended Order"],
            )

        df["Base Recommended Order"] = (
            pd.to_numeric(df["Base Recommended Order"], errors="coerce")
            .fillna(0)
            .astype(int)
        )
        df["Recommended Order"] = df["Base Recommended Order"]
    else:
        df["Back Ordered"] = 0
        df["Backorder Customers"] = 0

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
    selected_locations = col3.multiselect("Location", location_values)
    only_below_min = col4.checkbox("Only below min", value=False)
    only_allocated = col5.checkbox("Only allocated > 0", value=False)

    spring_categories = sorted(df["Spring Category"].dropna().unique().tolist())
    selected_spring_categories = st.multiselect("Spring Category", spring_categories)

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
        hide_factory=hide_factory,
        hide_dewalt=hide_dewalt,
        hide_miele=hide_miele,
    )

    if selected_spring_categories:
        filtered = filtered[filtered["Spring Category"].isin(selected_spring_categories)]

    sort_options = [
        "Back Ordered",
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

    sort_col = st.selectbox("Sort by", sort_options, index=0)
    sort_desc = st.toggle("Descending sort", value=True)

    if not filtered.empty:
        filtered = filtered.sort_values(sort_col, ascending=not sort_desc)

    k1, k2, k3, k4, k5, k6 = st.columns(6)
    k1.metric("Rows shown", f"{len(filtered):,}")
    k2.metric("Allocated units", f"{int(filtered['Qty Allocated'].sum()) if not filtered.empty else 0:,}")
    k3.metric("Units to Order", f"{int(filtered['Recommended Order'].sum()) if not filtered.empty else 0:,}")
    k4.metric("Urgent items", f"{int((filtered['Priority V2'] == '🔴 URGENT').sum()) if not filtered.empty else 0:,}")
    matched_count = int(df["Forecast Matched?"].sum()) if "Forecast Matched?" in df.columns else 0
    k5.metric("Forecast matches", f"{matched_count:,}")
    k6.metric("Units backordered", f"{int(filtered['Back Ordered'].sum()) if not filtered.empty else 0:,}")

    if forecast_loaded and demand_basis == "Custom Forecast Average":
        st.caption(
            f"Forecast Mode: {forecast_mode} | Demand Basis: {demand_basis} | "
            f"Forecast Months: {custom_forecast_months} | View: {table_view}"
        )
    else:
        st.caption(f"Forecast Mode: {forecast_mode} | Demand Basis: {demand_basis} | View: {table_view}")

    simple_review_columns = [
        "Part_Number", "Description", "POREF_SUPP", "Spring Category", "Qty on hand",
        "Qty Allocated", "Qty on Order", "Available", "Net After POs",
        "Back Ordered", "Recommended Order", "Priority V2"
    ]
    simple_review_columns = [c for c in simple_review_columns if c in filtered.columns]

    detailed_review_columns = [
        "Part_Number", "Description", "POREF_SUPP", "Type", "Loc", "Spring Category",
        "Qty on hand", "Qty Allocated", "Qty on Order", "Total On Order (All Locations)",
        "Available", "Net After POs", "Min",
        "Effective Min", "Max", "Demand_Per_Month_Used", "Forecast Average",
        "Forecast Months Used", "Target Stock", "Base Recommended Order",
        "Recommended Order", "EOQ", "6mAvg", "6mUsage", "12mAvg", "12mUsage",
        "Back Ordered", "Backorder Customers", "Priority V2", "Forecast Matched?"
    ]
    detailed_review_columns = [c for c in detailed_review_columns if c in filtered.columns]

    review_columns = simple_review_columns if table_view == "Simple" else detailed_review_columns

    table_df = filtered[review_columns].copy()
    table_df.insert(0, "View", False)
    table_df = table_df.rename(columns={"Priority V2": "Priority"})

    disabled_columns = [c for c in table_df.columns if c != "View"]

    edited_table = st.data_editor(
        table_df,
        width="stretch",
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

    render_demand_trend_preview(
        filtered_df=filtered,
        forecast_detail=forecast_detail,
        month_cols=month_cols,
        selected_part_number=selected_part_number,
    )

    csv_bytes = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download Order CSV",
        data=csv_bytes,
        file_name="order_list.csv",
        mime="text/csv",
    )

    with st.expander("Detected file structure"):
        st.write("Main Filter Column:", main_filter_col)
        st.write("Forecast File Loaded:", forecast_loaded)
        st.write("Back Order Report Loaded:", backorder_loaded)
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
