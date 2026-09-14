from typing import List, Tuple

import pandas as pd


# =========================================================
# 1. REMOVE EXACT DUPLICATES
# =========================================================

def remove_exact_duplicates(
    df: pd.DataFrame,
) -> Tuple[pd.DataFrame, int]:
    """
    Remove rows that are exact duplicates across every column.

    One copy of each identical row is retained.

    Returns:
        cleaned_df
        removed_count

    Primary-key conflicts are not handled by this function.
    """

    cleaned_df = (
        df.drop_duplicates(
            keep="first"
        )
        .copy()
    )

    removed_count = (
        len(df)
        - len(cleaned_df)
    )

    return (
        cleaned_df,
        int(removed_count),
    )


# =========================================================
# 2. FIND PK DUPLICATES
# =========================================================

def find_primary_key_duplicates(
    df: pd.DataFrame,
    pk_columns: List[str],
) -> pd.DataFrame:
    """
    Return all rows participating in a reviewed PK duplicate.

    No rows are removed.

    Conflicting PK duplicates must remain visible for later
    validation instead of being silently resolved.
    """

    if not pk_columns:

        return pd.DataFrame(
            columns=df.columns
        )

    missing_columns = [
        column
        for column in pk_columns
        if column not in df.columns
    ]

    if missing_columns:

        raise KeyError(
            "PK columns missing from "
            "standardized table: "
            + ", ".join(
                missing_columns
            )
        )

    duplicate_mask = (
        df.duplicated(
            subset=pk_columns,
            keep=False,
        )
    )

    return (
        df.loc[
            duplicate_mask
        ]
        .copy()
    )


# =========================================================
# 3. HANDLE TABLE DUPLICATES
# =========================================================

def handle_duplicates(
    df: pd.DataFrame,
    pk_columns: List[str],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Apply the approved duplicate policy to one table.

    Policy:
    1. Exact duplicate rows are removed.
    2. PK duplicates remaining after exact deduplication are
       detected but never automatically deleted.

    This preserves conflicting business records for validation.
    """

    (
        cleaned_df,
        exact_removed_count,
    ) = remove_exact_duplicates(
        df
    )

    pk_duplicates = (
        find_primary_key_duplicates(
            df=cleaned_df,
            pk_columns=pk_columns,
        )
    )

    report_df = pd.DataFrame(
        [
            {
                "exact_duplicates_removed":
                    exact_removed_count,

                "pk_duplicate_rows_remaining":
                    len(
                        pk_duplicates
                    ),
            }
        ]
    )

    return (
        cleaned_df,
        report_df,
    )