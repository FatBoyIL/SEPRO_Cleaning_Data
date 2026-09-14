from typing import Tuple

import pandas as pd


# =========================================================
# 1. NORMALIZE ONE TEXT SERIES
# =========================================================

def standardize_text_series(
    series: pd.Series,
) -> Tuple[pd.Series, int]:
    """
    Standardize basic text formatting in one Series.

    The function:
    - trims leading/trailing whitespace,
    - replaces tabs/newlines with spaces,
    - collapses repeated whitespace into one space.

    Missing values are preserved.

    Returns:
        cleaned_series:
            Standardized copy of the input Series.

        changed_count:
            Number of non-null values whose text representation changed.

    This function does not perform semantic value mapping and does
    not modify the original Series.
    """

    cleaned_series = (
        series.copy()
    )

    # Only transform non-null values.
    non_null_mask = (
        cleaned_series.notna()
    )

    if not non_null_mask.any():
        return (
            cleaned_series,
            0,
        )

    original_values = (
        cleaned_series.loc[
            non_null_mask
        ]
        .astype("string")
    )

    standardized_values = (
        original_values
        .str.replace(
            r"[\t\r\n]+",
            " ",
            regex=True,
        )
        .str.strip()
        .str.replace(
            r"\s+",
            " ",
            regex=True,
        )
    )

    # Count only values that actually changed.
    changed_mask = (
        original_values
        != standardized_values
    )

    changed_count = int(
        changed_mask.sum()
    )

    cleaned_series.loc[
        non_null_mask
    ] = standardized_values

    return (
        cleaned_series,
        changed_count,
    )


# =========================================================
# 2. DETECT TEXT-LIKE COLUMNS
# =========================================================

def _is_text_like_series(
    series: pd.Series,
) -> bool:
    """
    Check whether a Series is suitable for basic text standardization.

    Object and pandas string columns are treated as text-like.
    Numeric, boolean, date, and datetime columns are skipped.

    The function only inspects dtype metadata and does not change data.
    """

    return (
        pd.api.types.is_object_dtype(
            series.dtype
        )
        or pd.api.types.is_string_dtype(
            series.dtype
        )
    )


# =========================================================
# 3. STANDARDIZE TEXT FOR A DATAFRAME
# =========================================================

def standardize_text_columns(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Standardize basic formatting for all text-like columns in a DataFrame.

    Only object/string columns are processed.

    Returns:
        cleaned_df:
            A cleaned copy of the input DataFrame.

        change_report:
            Column-level summary of how many values changed.

    The original DataFrame is never modified.
    """

    cleaned_df = (
        df.copy()
    )

    change_rows = []

    for column_name in (
        cleaned_df.columns
    ):

        series = (
            cleaned_df[
                column_name
            ]
        )

        if not _is_text_like_series(
            series
        ):
            continue

        cleaned_series, changed_count = (
            standardize_text_series(
                series
            )
        )

        cleaned_df[
            column_name
        ] = cleaned_series

        if changed_count > 0:

            change_rows.append(
                {
                    "column_name":
                        column_name,

                    "text_values_changed":
                        changed_count,
                }
            )

    change_report = (
        pd.DataFrame(
            change_rows,
            columns=[
                "column_name",
                "text_values_changed",
            ],
        )
    )

    return (
        cleaned_df,
        change_report,
    )