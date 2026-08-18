import pandas as pd

# Shared Part Group / Part Type filtering logic (pure pandas - the matching
# Streamlit widgets live in ui/filters.py).
#
# "Part Group" (e.g. "L8 - LM 480 SERIES") and "Part Type" (e.g.
# "LM - LM ROTARY ALLOY") are two separate NetSuite fields. Part Type was
# added to the saved searches alongside the existing Part Group, so every
# ordering mode filters on both rather than one or the other.
#
# Everything here is column-NAME based, never positional, so a saved search
# reordering its exported columns (as the Units export did when Part Group
# moved) doesn't affect any of it.

PART_GROUP_COL = "Part Group"
PART_TYPE_COL = "Part Type"


def distinct_values(df: pd.DataFrame, column: str) -> list[str]:
    """
    Sorted, de-duplicated, non-blank values for a column.

    Returns [] when the column isn't in the uploaded export, so callers can
    render a disabled widget instead of blowing up.
    """
    if column not in df.columns:
        return []

    return sorted(
        {str(x).strip() for x in df[column].dropna().tolist() if str(x).strip() != ""}
    )


def apply_grouping_filters(
    df: pd.DataFrame,
    part_group_values: list | None = None,
    part_type_values: list | None = None,
) -> pd.DataFrame:
    """
    Apply the Part Group / Part Type selections. No-op for an empty
    selection or a column the export doesn't have.
    """
    filtered = df

    if part_group_values and PART_GROUP_COL in filtered.columns:
        filtered = filtered[
            filtered[PART_GROUP_COL].astype(str).str.strip().isin(part_group_values)
        ]

    if part_type_values and PART_TYPE_COL in filtered.columns:
        filtered = filtered[
            filtered[PART_TYPE_COL].astype(str).str.strip().isin(part_type_values)
        ]

    return filtered
