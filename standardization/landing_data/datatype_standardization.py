from decimal import Decimal, InvalidOperation
from typing import Dict, List, Tuple

import pandas as pd


# =========================================================
# 1. NORMALIZE TARGET TYPE NAME
# =========================================================

def _normalize_type_name(
    target_type: str,
) -> str:
    """
    Normalize datatype aliases from the reviewed schema.

    This allows the schema to use common names such as int,
    bigint, numeric, text, or timestamp while the transformation
    engine works with a smaller canonical type set.
    """

    value = (
        str(target_type)
        .strip()
        .lower()
    )

    aliases = {
        "str": "string",
        "text": "string",

        "int": "integer",
        "bigint": "integer",

        "float": "decimal",
        "numeric": "decimal",
        "money": "decimal",

        "bool": "boolean",

        "timestamp": "datetime"
    }

    return aliases.get(
        value,
        value,
    )


# =========================================================
# 2. CLEAN NUMERIC TEXT
# =========================================================

def _clean_numeric_text(
    series: pd.Series,
) -> pd.Series:
    """
    Prepare numeric-looking text for parsing.

    Standard thousands separators such as 1,250.50 are supported.

    Locale-specific formats such as 1.250,50 are intentionally not
    guessed automatically because that could change business values.
    """

    return (
        series.astype("string")
        .str.strip()
        .str.replace(
            ",",
            "",
            regex=False,
        )
    )


# =========================================================
# 3. DECIMAL PARSER
# =========================================================

def _parse_decimal(
    value,
):
    """
    Convert one non-null value to Decimal.

    Invalid values return pd.NA instead of raising an exception.
    """

    if pd.isna(value):
        return pd.NA

    text = (
        str(value)
        .strip()
        .replace(",", "")
    )

    try:
        return Decimal(text)

    except (
        InvalidOperation,
        ValueError,
    ):
        return pd.NA


# =========================================================
# 4. BOOLEAN PARSER
# =========================================================

def _parse_boolean(
    series: pd.Series,
) -> pd.Series:
    """
    Convert common boolean representations to pandas BooleanDtype.

    Supported true values:
        true, 1, yes, y, t

    Supported false values:
        false, 0, no, n, f
    """

    mapping = {
        "true": True,
        "1": True,
        "yes": True,
        "y": True,
        "t": True,

        "false": False,
        "0": False,
        "no": False,
        "n": False,
        "f": False,
    }

    output = pd.Series(
        pd.NA,
        index=series.index,
        dtype="boolean",
    )

    for index, value in series.items():

        if pd.isna(value):
            continue

        key = (
            str(value)
            .strip()
            .lower()
        )

        if key in mapping:

            output.at[
                index
            ] = mapping[key]

    return output


# =========================================================
# 5. CAST ONE SERIES
# =========================================================

def cast_series_to_type(
    series: pd.Series,
    target_type: str,
) -> Tuple[
    pd.Series,
    int,
    List[str],
]:
    """
    Convert one Series to the reviewed Silver datatype.

    Invalid non-null source values are converted to missing values and
    reported separately so they are never silently treated as valid.

    Returns:
        cleaned_series
        invalid_count
        invalid_examples
    """

    canonical_type = (
        _normalize_type_name(
            target_type
        )
    )

    original_non_null = (
        series.notna()
    )

    # -----------------------------------------------------
    # STRING
    # -----------------------------------------------------
    if canonical_type == "string":

        cleaned = (
            series.astype("string")
        )

    # -----------------------------------------------------
    # INTEGER
    # -----------------------------------------------------
    elif canonical_type == "integer":

        numeric = pd.to_numeric(
            _clean_numeric_text(
                series
            ),
            errors="coerce",
        )

        # Decimal values such as 12.5 are invalid integers.
        fractional_mask = (
            numeric.notna()
            & (
                numeric.mod(1)
                != 0
            )
        )

        numeric.loc[
            fractional_mask
        ] = pd.NA

        cleaned = (
            numeric.astype("Int64")
        )

    # -----------------------------------------------------
    # DECIMAL
    # -----------------------------------------------------
    elif canonical_type == "decimal":

        cleaned = (
            series.apply(
                _parse_decimal
            )
        )

    # -----------------------------------------------------
    # DATE
    # -----------------------------------------------------
    elif canonical_type == "date":

        parsed = pd.to_datetime(
            series.astype("string"),
            errors="coerce",
        )

        cleaned = (
            parsed.dt.date
        )

    # -----------------------------------------------------
    # DATETIME
    # -----------------------------------------------------
    elif canonical_type == "datetime":

        cleaned = pd.to_datetime(
            series.astype("string"),
            errors="coerce",
        )

    # -----------------------------------------------------
    # BOOLEAN
    # -----------------------------------------------------
    elif canonical_type == "boolean":

        cleaned = (
            _parse_boolean(
                series
            )
        )

    else:

        raise ValueError(
            f"Unsupported Silver datatype: "
            f"{target_type}"
        )

    converted_missing = pd.isna(
        cleaned
    )

    invalid_mask = (
        original_non_null
        & converted_missing
    )

    invalid_count = int(
        invalid_mask.sum()
    )

    invalid_examples = (
        series.loc[
            invalid_mask
        ]
        .astype("string")
        .drop_duplicates()
        .head(5)
        .tolist()
    )

    return (
        cleaned,
        invalid_count,
        invalid_examples,
    )


# =========================================================
# 6. STANDARDIZE TABLE DATATYPES
# =========================================================

def standardize_datatypes(
    df: pd.DataFrame,
    table_schema: Dict,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Apply reviewed datatypes to every Silver column in a table.

    The function uses the reviewed column name stored in:
        column_info["name"]

    Conversion failures are returned in the report and are not hidden.

    The original DataFrame is never modified.
    """

    cleaned_df = (
        df.copy()
    )

    report_rows = []

    columns = table_schema.get(
        "columns",
        {},
    )

    for source_column, column_info in (
        columns.items()
    ):

        silver_column = (
            column_info.get(
                "name",
                source_column,
            )
        )

        target_type = (
            column_info.get(
                "datatype",
                "string",
            )
        )

        if silver_column not in cleaned_df.columns:
            raise KeyError(
                f"Reviewed column "
                f"'{silver_column}' not found "
                f"during datatype standardization."
            )

        (
            cleaned_series,
            invalid_count,
            invalid_examples,
        ) = cast_series_to_type(
            series=cleaned_df[
                silver_column
            ],
            target_type=target_type,
        )

        cleaned_df[
            silver_column
        ] = cleaned_series

        report_rows.append(
            {
                "column_name":
                    silver_column,

                "target_datatype":
                    target_type,

                "invalid_value_count":
                    invalid_count,

                "invalid_examples":
                    " | ".join(
                        invalid_examples
                    ),
            }
        )

    report_df = pd.DataFrame(
        report_rows,
        columns=[
            "column_name",
            "target_datatype",
            "invalid_value_count",
            "invalid_examples",
        ],
    )

    return (
        cleaned_df,
        report_df,
    )