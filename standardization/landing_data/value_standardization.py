from typing import Dict, Tuple

import pandas as pd


# =========================================================
# 1. NORMALIZE MAPPING KEY
# =========================================================

def _normalize_mapping_key(
    value,
) -> str:
    """
    Normalize a business value before looking it up in a mapping.

    Matching is case-insensitive and ignores leading/trailing
    whitespace.

    The original DataFrame value is not modified by this helper.
    """

    return (
        str(value)
        .strip()
        .lower()
    )


# =========================================================
# 2. STANDARDIZE ONE SERIES USING A MAPPING
# =========================================================

def standardize_value_series(
    series: pd.Series,
    mapping: Dict,
) -> Tuple[pd.Series, int]:
    """
    Apply an approved business-value mapping to one Series.

    Example:
        Vietnam  -> VN
        Viet Nam -> VN

    Only values explicitly represented in the mapping are changed.
    Missing values and unmapped values are preserved.

    Returns a cleaned Series and the number of changed values.
    """

    cleaned_series = (
        series.copy()
    )

    normalized_mapping = {
        _normalize_mapping_key(
            source_value
        ): target_value

        for source_value, target_value
        in mapping.items()
    }

    changed_count = 0

    for index, value in (
        cleaned_series.items()
    ):

        if pd.isna(value):
            continue

        lookup_key = (
            _normalize_mapping_key(
                value
            )
        )

        if lookup_key not in normalized_mapping:
            continue

        target_value = (
            normalized_mapping[
                lookup_key
            ]
        )

        # Compare string representations only to decide
        # whether a visible business value actually changed.
        if str(value) != str(target_value):

            cleaned_series.at[
                index
            ] = target_value

            changed_count += 1

    return (
        cleaned_series,
        changed_count,
    )


# =========================================================
# 3. APPLY TABLE VALUE MAPPINGS
# =========================================================

def apply_value_mappings(
    df: pd.DataFrame,
    table_name: str,
    rules: Dict,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Apply configured business-value mappings to one table.

    Mapping rules are read from:
        rules["value_mappings"][table_name]

    Column names in the rules should use the reviewed Silver
    column names.

    The original DataFrame is never modified.
    """

    cleaned_df = (
        df.copy()
    )

    change_rows = []

    table_rules = (
        rules
        .get(
            "value_mappings",
            {},
        )
        .get(
            table_name,
            {},
        )
    )

    for column_name, mapping in (
        table_rules.items()
    ):

        if column_name not in cleaned_df.columns:
            raise KeyError(
                f"Value mapping column "
                f"'{column_name}' not found "
                f"in table '{table_name}'."
            )

        (
            cleaned_series,
            changed_count,
        ) = standardize_value_series(
            series=cleaned_df[
                column_name
            ],
            mapping=mapping,
        )

        cleaned_df[
            column_name
        ] = cleaned_series

        if changed_count > 0:

            change_rows.append(
                {
                    "column_name":
                        column_name,

                    "value_mappings_changed":
                        changed_count,
                }
            )

    change_report = pd.DataFrame(
        change_rows,
        columns=[
            "column_name",
            "value_mappings_changed",
        ],
    )

    return (
        cleaned_df,
        change_report,
    )