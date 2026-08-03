import pandas as pd
import streamlit as st


def render_demand_trend_preview(
    filtered_df: pd.DataFrame,
    forecast_detail: pd.DataFrame | None,
    month_cols: list[str],
    selected_part_number: str | None = None,
    selectbox_key: str = "selected_part_for_chart",
    title: str = "Demand Trend Preview",
):
    with st.expander(title, expanded=False):
        if forecast_detail is None or forecast_detail.empty:
            st.info("Upload a forecast file to enable trend preview.")
            return

        available_parts = (
            filtered_df["Part_Number"]
            .dropna()
            .astype(str)
            .unique()
            .tolist()
        )
        available_parts = sorted([p for p in available_parts if p.strip() != ""])

        if not available_parts:
            st.info("No filtered parts available for trend preview.")
            return

        default_part = (
            selected_part_number
            if selected_part_number in available_parts
            else available_parts[0]
        )

        chart_part = st.selectbox(
            "Select part for trend preview",
            available_parts,
            index=available_parts.index(default_part),
            key=selectbox_key,
        )

        chart_rows = forecast_detail[forecast_detail["Part_Number"] == chart_part].copy()

        if chart_rows.empty:
            st.info("No forecast history found for the selected part.")
            return

        trend = chart_rows[month_cols].sum(axis=0)

        trend_df = pd.DataFrame(
            {
                "Month": list(reversed(month_cols)),
                "Usage": list(reversed(trend.values.tolist())),
            }
        )

        st.line_chart(trend_df.set_index("Month"))
        st.caption(
            f"Trend preview for {chart_part}. Left side is older months; "
            f"right side is the most recent month from the forecast export."
        )