import pandas as pd
import streamlit as st

from utils.filters import (
    PART_GROUP_COL,
    PART_TYPE_COL,
    apply_grouping_filters,
    distinct_values,
)

# Streamlit widgets for the shared Part Group / Part Type filters. The
# matching pandas logic lives in utils/filters.py so services can reuse it
# without importing Streamlit UI code.

__all__ = ["render_grouping_filters", "apply_grouping_filters"]


def render_grouping_filters(
    df: pd.DataFrame,
    key_prefix: str,
    container=None,
) -> tuple[list, list]:
    """
    Render the Part Group / Part Type multiselects side by side.

    Returns (selected_part_groups, selected_part_types). Either widget is
    disabled when the uploaded export doesn't carry that column.
    """
    target = container if container is not None else st
    group_col, type_col = target.columns(2)

    part_group_values = distinct_values(df, PART_GROUP_COL)
    selected_part_groups = group_col.multiselect(
        PART_GROUP_COL,
        part_group_values,
        key=f"{key_prefix}_part_group",
        help="NetSuite part grouping (e.g. L8 - LM 480 SERIES, LP - LAWNMASTER PARTS).",
        disabled=not part_group_values,
    )

    part_type_values = distinct_values(df, PART_TYPE_COL)
    selected_part_types = type_col.multiselect(
        PART_TYPE_COL,
        part_type_values,
        key=f"{key_prefix}_part_type",
        help="NetSuite product type (e.g. LM - LM ROTARY ALLOY, LP - LM PARTS).",
        disabled=not part_type_values,
    )

    return selected_part_groups, selected_part_types
