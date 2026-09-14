from decimal import Decimal, InvalidOperation
from typing import Dict, Tuple

import pandas as pd


# =========================================================
# 1. NORMALIZE CURRENCY CODE
# =========================================================

def normalize_currency_code(
    value,
    aliases: Dict,
):
    """
    Normalize one currency value using configured aliases.

    Example:
        US$ -> USD
        usd -> USD
        VNĐ -> VND

    Missing values remain missing.
    """

    if pd.isna(value):
        return pd.NA

    text = (
        str(value)
        .strip()
    )

    normalized_aliases = {
        str(source)
        .strip()
        .upper():
            target

        for source, target
        in aliases.items()
    }

    lookup_key = (
        text.upper()
    )

    return normalized_aliases.get(
        lookup_key,
        lookup_key,
    )


# =========================================================
# 2. SAFE DECIMAL
# =========================================================

def _safe_decimal(
    value,
):
    """
    Convert one monetary value to Decimal without using binary float.

    Invalid values return pd.NA.
    """

    if pd.isna(value):
        return pd.NA

    try:
        return Decimal(
            str(value)
            .strip()
            .replace(",", "")
        )

    except (
        InvalidOperation,
        ValueError,
    ):
        return pd.NA


# =========================================================
# 3. PREPARE FX TABLE
# =========================================================

def prepare_fx_rates(
    fx_df: pd.DataFrame,
    rules: Dict,
) -> pd.DataFrame:
    """
    Prepare the reviewed FX-rate table for currency conversion.

    The returned DataFrame contains internal standardized columns:
        _fx_date
        _fx_currency
        _fx_rate

    Invalid FX rows are excluded from conversion input.
    """

    global_rules = rules.get(
        "_global",
        {},
    )

    fx_rules = global_rules.get(
        "fx",
        {},
    )

    aliases = global_rules.get(
        "currency_aliases",
        {},
    )

    date_column = fx_rules[
        "date_column"
    ]

    currency_column = fx_rules[
        "currency_column"
    ]

    rate_column = fx_rules[
        "rate_column"
    ]

    required = {
        date_column,
        currency_column,
        rate_column,
    }

    missing = (
        required
        - set(fx_df.columns)
    )

    if missing:

        raise KeyError(
            "FX table missing columns: "
            + ", ".join(
                sorted(missing)
            )
        )

    prepared = pd.DataFrame(
        {
            "_fx_date":
                pd.to_datetime(
                    fx_df[
                        date_column
                    ],
                    errors="coerce",
                )
                .dt.normalize(),

            "_fx_currency":
                fx_df[
                    currency_column
                ].apply(
                    lambda value:
                    normalize_currency_code(
                        value,
                        aliases,
                    )
                ),

            "_fx_rate":
                fx_df[
                    rate_column
                ].apply(
                    _safe_decimal
                ),
        }
    )

    prepared = prepared.dropna(
        subset=[
            "_fx_date",
            "_fx_currency",
            "_fx_rate",
        ]
    )

    prepared = (
        prepared
        .sort_values(
            [
                "_fx_currency",
                "_fx_date",
            ]
        )
        .drop_duplicates(
            subset=[
                "_fx_currency",
                "_fx_date",
            ],
            keep="last",
        )
        .reset_index(
            drop=True
        )
    )

    return prepared


# =========================================================
# 4. FIND FX RATES FOR TRANSACTION ROWS
# =========================================================

def _find_transaction_rates(
    currency_series: pd.Series,
    date_series: pd.Series,
    fx_rates: pd.DataFrame,
    base_currency: str,
    missing_rate_rule: str,
) -> pd.Series:
    """
    Match one FX rate to every transaction row.

    Supported missing-rate rules:
        exact
        previous_available_rate

    VND/base-currency rows always receive rate 1.
    """

    result = pd.Series(
        pd.NA,
        index=currency_series.index,
        dtype="object",
    )

    normalized_dates = (
        pd.to_datetime(
            date_series,
            errors="coerce",
        )
        .dt.normalize()
    )

    # Base-currency transactions require no FX conversion.
    base_mask = (
        currency_series
        == base_currency
    )

    result.loc[
        base_mask
    ] = Decimal("1")

    foreign_mask = (
        currency_series.notna()
        & normalized_dates.notna()
        & ~base_mask
    )

    foreign_currencies = (
        currency_series.loc[
            foreign_mask
        ]
        .dropna()
        .unique()
        .tolist()
    )

    for currency_code in foreign_currencies:

        row_mask = (
            foreign_mask
            & (
                currency_series
                == currency_code
            )
        )

        transaction_rows = pd.DataFrame(
            {
                "_source_index":
                    currency_series.index[
                        row_mask
                    ],

                "_transaction_date":
                    normalized_dates.loc[
                        row_mask
                    ].values,
            }
        )

        transaction_rows = (
            transaction_rows
            .sort_values(
                "_transaction_date"
            )
        )

        currency_fx = (
            fx_rates[
                fx_rates[
                    "_fx_currency"
                ]
                == currency_code
            ]
            .copy()
            .sort_values(
                "_fx_date"
            )
        )

        if currency_fx.empty:
            continue

        if (
            missing_rate_rule
            == "exact"
        ):

            merged = (
                transaction_rows
                .merge(
                    currency_fx,
                    left_on=(
                        "_transaction_date"
                    ),
                    right_on=(
                        "_fx_date"
                    ),
                    how="left",
                )
            )

        elif (
            missing_rate_rule
            == "previous_available_rate"
        ):

            merged = pd.merge_asof(
                transaction_rows,
                currency_fx,
                left_on=(
                    "_transaction_date"
                ),
                right_on=(
                    "_fx_date"
                ),
                direction="backward",
            )

        else:

            raise ValueError(
                "Unsupported missing FX "
                f"rate rule: "
                f"{missing_rate_rule}"
            )

        for _, row in merged.iterrows():

            result.at[
                row["_source_index"]
            ] = row["_fx_rate"]

    return result


# =========================================================
# 5. CURRENCY STANDARDIZATION FOR ONE TABLE
# =========================================================

def standardize_currency_table(
    df: pd.DataFrame,
    table_name: str,
    rules: Dict,
    fx_rates: pd.DataFrame = None,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Standardize currency fields and create base-currency amounts.

    Currency processing runs only when the table is configured under:
        rules["currency"][table_name]

    Source monetary values are preserved. New VND/base-currency
    output columns are added instead of overwriting source amounts.
    """

    cleaned_df = (
        df.copy()
    )

    table_rule = (
        rules
        .get(
            "currency",
            {},
        )
        .get(
            table_name
        )
    )

    # Table has no monetary conversion rule.
    if not table_rule:

        return (
            cleaned_df,
            pd.DataFrame(
                columns=[
                    "table_name",
                    "converted_row_count",
                    "missing_fx_count",
                    "invalid_currency_count",
                ]
            ),
        )

    global_rules = rules.get(
        "_global",
        {},
    )

    aliases = global_rules.get(
        "currency_aliases",
        {},
    )

    base_currency = (
        global_rules.get(
            "base_currency",
            "VND",
        )
    )

    fx_rules = global_rules.get(
        "fx",
        {},
    )

    missing_rate_rule = (
        fx_rules.get(
            "missing_rate_rule",
            "previous_available_rate",
        )
    )

    currency_column = table_rule[
        "currency_column"
    ]

    date_column = table_rule[
        "date_column"
    ]

    amount_columns = table_rule.get(
        "amount_columns",
        {},
    )

    currency_code_column = (
        table_rule.get(
            "currency_code_column",
            "currency_code",
        )
    )

    fx_rate_column = (
        table_rule.get(
            "fx_rate_column",
            "fx_rate_to_vnd",
        )
    )

    if currency_column not in cleaned_df.columns:
        raise KeyError(
            f"Currency column "
            f"'{currency_column}' not found "
            f"in '{table_name}'."
        )

    if date_column not in cleaned_df.columns:
        raise KeyError(
            f"Currency date column "
            f"'{date_column}' not found "
            f"in '{table_name}'."
        )

    # Preserve the source currency and add a standardized ISO-like code.
    cleaned_df[
        currency_code_column
    ] = (
        cleaned_df[
            currency_column
        ].apply(
            lambda value:
            normalize_currency_code(
                value,
                aliases,
            )
        )
    )

    if fx_rates is None:

        foreign_exists = (
            cleaned_df[
                currency_code_column
            ]
            .dropna()
            .ne(
                base_currency
            )
            .any()
        )

        if foreign_exists:

            raise ValueError(
                f"FX rates are required for "
                f"table '{table_name}'."
            )

        fx_rates = pd.DataFrame(
            columns=[
                "_fx_date",
                "_fx_currency",
                "_fx_rate",
            ]
        )

    rates = _find_transaction_rates(
        currency_series=cleaned_df[
            currency_code_column
        ],
        date_series=cleaned_df[
            date_column
        ],
        fx_rates=fx_rates,
        base_currency=base_currency,
        missing_rate_rule=(
            missing_rate_rule
        ),
    )

    cleaned_df[
        fx_rate_column
    ] = rates

    converted_mask = pd.Series(
        False,
        index=cleaned_df.index,
    )

    for source_column, output_column in (
        amount_columns.items()
    ):

        if source_column not in cleaned_df.columns:

            raise KeyError(
                f"Monetary column "
                f"'{source_column}' not found "
                f"in '{table_name}'."
            )

        converted_values = []

        for amount, rate in zip(
            cleaned_df[
                source_column
            ],
            rates,
        ):

            decimal_amount = (
                _safe_decimal(
                    amount
                )
            )

            decimal_rate = (
                _safe_decimal(
                    rate
                )
            )

            if (
                pd.isna(decimal_amount)
                or pd.isna(decimal_rate)
            ):

                converted_values.append(
                    pd.NA
                )

            else:

                converted_values.append(
                    decimal_amount
                    * decimal_rate
                )

        cleaned_df[
            output_column
        ] = converted_values

        converted_mask = (
            converted_mask
            | cleaned_df[
                output_column
            ].notna()
        )

    foreign_mask = (
        cleaned_df[
            currency_code_column
        ].notna()
        & (
            cleaned_df[
                currency_code_column
            ]
            != base_currency
        )
    )

    missing_fx_count = int(
        (
            foreign_mask
            & cleaned_df[
                fx_rate_column
            ].isna()
        ).sum()
    )

    # A code is considered invalid when it is not base currency
    # and no corresponding FX currency exists at all.
    known_fx_currencies = set(
        fx_rates[
            "_fx_currency"
        ].dropna()
    )

    invalid_currency_count = int(
        (
            cleaned_df[
                currency_code_column
            ].notna()
            & (
                cleaned_df[
                    currency_code_column
                ]
                != base_currency
            )
            & ~cleaned_df[
                currency_code_column
            ].isin(
                known_fx_currencies
            )
        ).sum()
    )

    report_df = pd.DataFrame(
        [
            {
                "table_name":
                    table_name,

                "converted_row_count":
                    int(
                        converted_mask.sum()
                    ),

                "missing_fx_count":
                    missing_fx_count,

                "invalid_currency_count":
                    invalid_currency_count,
            }
        ]
    )

    return (
        cleaned_df,
        report_df,
    )