import numpy as np
import streamlit as st

from services.bunnings_service import (
    build_bunnings_woh_estimate,
    clean_bunnings_file_cached,
    load_bunnings_forecast_by_loc_cached,
)
from utils.helpers import get_uploaded_file_bytes


def render_bunnings_mode() -> None:
    """
    Render the Bunnings workflow.
    """
    st.subheader("Bunnings")
    st.caption(
        "Direct Bunnings workflow. Upload the Bunnings spreadsheet and the raw forecasting dataset."
    )

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
        bunnings_forecast_df = load_bunnings_forecast_by_loc_cached(forecast_bytes, forecast_name)

    with st.spinner("Calculating Weeks on Hand..."):
        bunnings_view_df = build_bunnings_woh_estimate(bunnings_df, bunnings_forecast_df)

    status_options = ["🔴 URGENT", "🟠 RISK", "🟢 OK"]
    selected_bunnings_status = st.multiselect("Filter status", status_options, default=status_options)

    show_only_matched_loc = st.checkbox("Only show rows with recognised VU / PV location mapping", value=False)
    show_only_active = st.checkbox("Only show non-empty status rows", value=False)
    bunnings_search = st.text_input("Search Bunnings SKU / Steelfort SKU / description", key="bunnings_search_direct")

    filtered = bunnings_view_df.copy()

    if selected_bunnings_status:
        filtered = filtered[filtered["Bunnings_Status"].isin(selected_bunnings_status)]

    if show_only_matched_loc:
        filtered = filtered[filtered["Forecast_Loc"].isin(["DC", "10"])]

    if show_only_active:
        filtered = filtered[filtered["Status"].astype(str).str.strip() != ""]

    if bunnings_search:
        q = bunnings_search.strip().lower()
        filtered = filtered[
            filtered["SKU"].astype(str).str.lower().str.contains(q, na=False) |
            filtered["Steelfort_Sku"].astype(str).str.lower().str.contains(q, na=False) |
            filtered["Match_Part"].astype(str).str.lower().str.contains(q, na=False) |
            filtered["Item_Description"].astype(str).str.lower().str.contains(q, na=False)
        ]

    filtered = filtered.sort_values(
        by=["Weeks_on_Hand", "Weekly_Usage"],
        ascending=[True, False],
        na_position="last",
    )

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Rows shown", f"{len(filtered):,}")
    m2.metric("Urgent items", f"{int((filtered['Bunnings_Status'] == '🔴 URGENT').sum()):,}")
    m3.metric("Risk items", f"{int((filtered['Bunnings_Status'] == '🟠 RISK').sum()):,}")
    m4.metric(
        "Avg WOH",
        f"{filtered['Weeks_on_Hand'].replace([np.inf, -np.inf], np.nan).mean():.2f}"
        if filtered["Weeks_on_Hand"].notna().any() else "0.00"
    )

    display_columns = [
        "Bunnings_Item_Number",
        "SKU",
        "Steelfort_Sku",
        "Match_Part",
        "Forecast_Loc",
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
    ]
    display_columns = [c for c in display_columns if c in filtered.columns]

    st.dataframe(filtered[display_columns], use_container_width=True, hide_index=True)

    export_csv = filtered.to_csv(index=False).encode("utf-8")
    st.download_button(
        "⬇️ Download Bunnings WOH CSV",
        data=export_csv,
        file_name="bunnings_weeks_on_hand.csv",
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