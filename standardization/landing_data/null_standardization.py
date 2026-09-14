from typing import Iterable, Tuple

import pandas as pd

from config.table_config import NULL_MARKERS


# =========================================================
# 1. NORMALIZE TEXT FOR NULL COMPARISON
# =========================================================

def _normalize_for_null_check(
    series: pd.Series,
) -> pd.Series:
    """
    Create a temporary normalized text representation for NULL checking.

    The function:
    - converts values to pandas string type,
    - trims leading/trailing whitespace,
    - collapses repeated whitespace,
    - converts text to lowercase.

    This helper does not modify the original Series.
    """

    return (
        series.astype("string")
        .str.strip()
        .str.replace(
            r"\s+",
            " ",
            regex=True,
        )
        .str.lower()
    )


# =========================================================
# 2. FIND FAKE NULL VALUES IN ONE COLUMN
# =========================================================

def find_null_markers(
    series: pd.Series,
    null_markers: Iterable[str] = NULL_MARKERS,
) -> pd.Series:
    """
    Return a boolean mask identifying configured fake-NULL text values.

    Real database NULL values are not marked by this function because
    they are already missing values. Only non-null textual placeholders
    such as 'N/A', 'NULL', '-' or an empty string are detected.

    The source Series is never modified.
    """

    real_null_mask = (
        series.isna()
    )

    normalized = (
        _normalize_for_null_check(
            series
        )
    )

    marker_set = {
        str(marker)
        .strip()
        .lower()
        for marker in null_markers
    }

    marker_mask = (
        ~real_null_mask
        & normalized.isin(
            marker_set
        )
    )

    return marker_mask


# =========================================================
# 3. STANDARDIZE NULLS IN ONE COLUMN
# =========================================================

def standardize_null_series(
    series: pd.Series,
    null_markers: Iterable[str] = NULL_MARKERS,
) -> Tuple[pd.Series, int]:
    """
    Convert configured fake-NULL markers in one Series to pd.NA.

    Returns:
        cleaned_series:
            A copy of the input Series where fake-NULL markers
            have been converted to pd.NA.

        changed_count:
            Number of values converted from a fake-NULL marker
            to a real missing value.

    Existing real NULL values are preserved and are not counted
    as changed values.
    """

    cleaned_series = (
        series.copy()
    )

    marker_mask = (
        find_null_markers(
            series=cleaned_series,
            null_markers=null_markers,
        )
    )

    changed_count = int(
        marker_mask.sum()
    )

    cleaned_series.loc[
        marker_mask
    ] = pd.NA

    return (
        cleaned_series,
        changed_count,
    )


# =========================================================
# 4. STANDARDIZE NULLS FOR A DATAFRAME
# =========================================================

def standardize_null_markers(
    df: pd.DataFrame,
    null_markers: Iterable[str] = NULL_MARKERS,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Standardize fake-NULL values across all columns of a DataFrame.

    Only columns that contain fake-NULL markers are changed.

    Returns:
        cleaned_df:
            A cleaned copy of the input DataFrame.

        change_report:
            A column-level summary containing the number of fake-NULL
            values converted to real NULL values.

    The original DataFrame is never modified.
    """

    cleaned_df = (
        df.copy()
    )

    change_rows = []

    for column_name in (
        cleaned_df.columns
    ):

        cleaned_series, changed_count = (
            standardize_null_series(
                series=cleaned_df[
                    column_name
                ],
                null_markers=null_markers,
            )
        )

        cleaned_df[
            column_name
        ] = cleaned_series

        # Only record columns where an actual standardization
        # occurred so the report remains concise.
        if changed_count > 0:

            change_rows.append(
                {
                    "column_name":
                        column_name,

                    "null_markers_changed":
                        changed_count,
                }
            )

    change_report = (
        pd.DataFrame(
            change_rows,
            columns=[
                "column_name",
                "null_markers_changed",
            ],
        )
    )

    return (
        cleaned_df,
        change_report,
    )