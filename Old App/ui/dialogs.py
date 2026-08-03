import numpy as np
import pandas as pd
import streamlit as st


@st.dialog("Part Details", width="medium")
def show_part_details_dialog(selected_row: pd.Series, demand_basis: str):
    """
    Show the centered part details popup.
    """
    forecast_average_value = selected_row.get(
        "Forecast Average",
        selected_row.get("Demand_Per_Month_Used", 0)
    )

    def format_value(value):
        if pd.isna(value):
            return ""
        if isinstance(value, (int, np.integer)):
            return f"{int(value):,}"
        if isinstance(value, (float, np.floating)):
            if float(value).is_integer():
                return f"{int(value):,}"
            return f"{value:,.2f}"
        return str(value)

    st.markdown(
        """
        <style>
        div[data-testid="stDialog"] section[role="dialog"] {
            max-width: 780px;
        }

        .popup-field {
            background: linear-gradient(180deg, #141a24 0%, #10151d 100%);
            border: 1px solid rgba(255,255,255,0.08);
            border-radius: 14px;
            padding: 12px 14px;
            margin-bottom: 12px;
            box-shadow: 0 8px 24px rgba(0,0,0,0.22);
            min-height: 86px;
        }

        .popup-label {
            font-size: 0.78rem;
            color: #9aa4b2;
            margin-bottom: 6px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }

        .popup-value {
            font-size: 0.98rem;
            color: #f3f4f6;
            word-break: break-word;
        }
        </style>
        """,
        unsafe_allow_html=True
    )

    col1, col2 = st.columns(2)

    with col1:
        st.markdown(f"""
            <div class="popup-field">
                <div class="popup-label">Part #</div>
                <div class="popup-value">{format_value(selected_row.get("Part_Number", ""))}</div>
            </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
            <div class="popup-field">
                <div class="popup-label">Description</div>
                <div class="popup-value">{format_value(selected_row.get("Description", ""))}</div>
            </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
            <div class="popup-field">
                <div class="popup-label">Supplier</div>
                <div class="popup-value">{format_value(selected_row.get("POREF_SUPP", ""))}</div>
            </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
            <div class="popup-field">
                <div class="popup-label">Qty on Order</div>
                <div class="popup-value">{format_value(selected_row.get("Qty on Order", ""))}</div>
            </div>
        """, unsafe_allow_html=True)

    with col2:
        st.markdown(f"""
            <div class="popup-field">
                <div class="popup-label">Available</div>
                <div class="popup-value">{format_value(selected_row.get("Available", ""))}</div>
            </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
            <div class="popup-field">
                <div class="popup-label">Recommended Order</div>
                <div class="popup-value">{format_value(selected_row.get("Recommended Order", ""))}</div>
            </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
            <div class="popup-field">
                <div class="popup-label">EOQ</div>
                <div class="popup-value">{format_value(selected_row.get("EOQ", ""))}</div>
            </div>
        """, unsafe_allow_html=True)

        st.markdown(f"""
            <div class="popup-field">
                <div class="popup-label">Forecast / Avg Used</div>
                <div class="popup-value">{format_value(forecast_average_value)}</div>
            </div>
        """, unsafe_allow_html=True)

    st.caption(f"Demand basis currently in use: {demand_basis}")