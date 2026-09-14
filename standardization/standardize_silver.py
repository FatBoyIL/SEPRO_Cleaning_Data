import json
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from standardization.landing_data.null_standardization import (
    standardize_null_markers,
)

from standardization.landing_data.text_standardization import (
    standardize_text_columns,
)

from standardization.landing_data.value_standardization import (
    apply_value_mappings,
)

from standardization.landing_data.datatype_standardization import (
    standardize_datatypes,
)

from standardization.landing_data.currency_standardization import (
    prepare_fx_rates,
    standardize_currency_table,
)

from standardization.landing_data.duplicate_handling import (
    handle_duplicates,
)


# =========================================================
# 1. LOAD JSON FILE
# =========================================================

def load_json_file(
    path: Path,
) -> Dict:
    """
    Load one UTF-8 JSON configuration file.

    Used for both the reviewed Silver schema and
    standardization business rules.
    """

    with open(
        path,
        "r",
        encoding="utf-8",
    ) as file:

        return json.load(
            file
        )


# =========================================================
# 2. RENAME COLUMNS FROM REVIEWED SCHEMA
# =========================================================

def rename_columns_from_schema(
    df: pd.DataFrame,
    table_schema: Dict,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Rename Bronze columns to their reviewed Silver names.

    Source column names come from the keys under:
        table_schema["columns"]

    Target names come from:
        column_info["name"]

    Name collisions are rejected instead of silently overwriting data.
    """

    cleaned_df = (
        df.copy()
    )

    rename_map = {}

    for source_column, column_info in (
        table_schema
        .get(
            "columns",
            {},
        )
        .items()
    ):

        if source_column not in cleaned_df.columns:

            raise KeyError(
                f"Source column "
                f"'{source_column}' not found."
            )

        target_column = (
            column_info.get(
                "name",
                source_column,
            )
        )

        rename_map[
            source_column
        ] = target_column

    target_names = list(
        rename_map.values()
    )

    if (
        len(target_names)
        != len(set(target_names))
    ):

        raise ValueError(
            "Reviewed Silver schema contains "
            "duplicate target column names."
        )

    changed_rows = [
        {
            "source_column":
                source_column,

            "silver_column":
                target_column,
        }

        for source_column, target_column
        in rename_map.items()

        if source_column
        != target_column
    ]

    cleaned_df = (
        cleaned_df.rename(
            columns=rename_map
        )
    )

    report_df = pd.DataFrame(
        changed_rows,
        columns=[
            "source_column",
            "silver_column",
        ],
    )

    return (
        cleaned_df,
        report_df,
    )


# =========================================================
# 3. RESOLVE REVIEWED PK TO SILVER COLUMN NAMES
# =========================================================

def resolve_primary_key_columns(
    table_schema: Dict,
) -> List[str]:
    """
    Convert reviewed PK names to final Silver column names.

    This supports schema proposals where PK names were originally
    inferred from Bronze/source column names.
    """

    columns = table_schema.get(
        "columns",
        {},
    )

    source_to_silver = {
        source_column:
            column_info.get(
                "name",
                source_column,
            )

        for source_column, column_info
        in columns.items()
    }

    primary_keys = (
        table_schema
        .get(
            "table",
            {},
        )
        .get(
            "primary_key",
            [],
        )
    )

    return [
        source_to_silver.get(
            pk_column,
            pk_column,
        )

        for pk_column
        in primary_keys
    ]


# =========================================================
# 4. STANDARDIZE BASE TABLE
# =========================================================

def standardize_base_table(
    df: pd.DataFrame,
    table_name: str,
    table_schema: Dict,
    rules: Dict,
) -> Tuple[pd.DataFrame, Dict]:
    """
    Apply all table transformations that do not depend on another table.

    Order:
        1. NULL
        2. Text
        3. Column rename
        4. Business value mapping
        5. Datatype

    Currency is intentionally performed later because it may depend
    on the standardized FX-rate table.
    """

    audit = {}

    # -----------------------------------------------------
    # 1. NULL
    # -----------------------------------------------------
    (
        working_df,
        null_report,
    ) = standardize_null_markers(
        df
    )

    audit[
        "null"
    ] = null_report

    # -----------------------------------------------------
    # 2. TEXT
    # -----------------------------------------------------
    (
        working_df,
        text_report,
    ) = standardize_text_columns(
        working_df
    )

    audit[
        "text"
    ] = text_report

    # -----------------------------------------------------
    # 3. COLUMN NAMES
    # -----------------------------------------------------
    (
        working_df,
        rename_report,
    ) = rename_columns_from_schema(
        df=working_df,
        table_schema=table_schema,
    )

    audit[
        "rename"
    ] = rename_report

    # -----------------------------------------------------
    # 4. BUSINESS VALUES
    # -----------------------------------------------------
    (
        working_df,
        value_report,
    ) = apply_value_mappings(
        df=working_df,
        table_name=table_name,
        rules=rules,
    )

    audit[
        "value"
    ] = value_report

    # -----------------------------------------------------
    # 5. DATATYPES
    # -----------------------------------------------------
    (
        working_df,
        datatype_report,
    ) = standardize_datatypes(
        df=working_df,
        table_schema=table_schema,
    )

    audit[
        "datatype"
    ] = datatype_report

    return (
        working_df,
        audit,
    )


# =========================================================
# 5. STANDARDIZE ALL TABLES
# =========================================================

def standardize_all_tables(
    all_tables: Dict[str, pd.DataFrame],
    schema_contract: Dict,
    rules: Dict,
) -> Tuple[
    Dict[str, pd.DataFrame],
    Dict[str, Dict],
]:
    """
    Standardize every reviewed Bronze table into an in-memory
    Silver-ready DataFrame.

    No SQL tables are written here.

    High-level order:
        Phase A:
            NULL
            Text
            Rename
            Value
            Datatype

        Phase B:
            Currency

        Phase C:
            Duplicate handling

    Returns:
        standardized_tables
        audit_reports
    """

    base_tables = {}

    audit_reports = {}

    # =====================================================
    # PHASE A
    # Independent table transformations
    # =====================================================

    for table_name, source_df in (
        all_tables.items()
    ):

        if table_name not in schema_contract:

            raise KeyError(
                f"Table '{table_name}' "
                f"is missing from "
                f"silver_schema_review.json."
            )

        (
            cleaned_df,
            table_audit,
        ) = standardize_base_table(
            df=source_df,
            table_name=table_name,
            table_schema=(
                schema_contract[
                    table_name
                ]
            ),
            rules=rules,
        )

        base_tables[
            table_name
        ] = cleaned_df

        audit_reports[
            table_name
        ] = table_audit

    # =====================================================
    # PREPARE FX DATA
    # =====================================================

    currency_rules = rules.get(
        "currency",
        {},
    )

    prepared_fx = None

    if currency_rules:

        fx_table_name = (
            rules
            .get(
                "_global",
                {},
            )
            .get(
                "fx",
                {},
            )
            .get(
                "rate_table"
            )
        )

        if (
            fx_table_name
            not in base_tables
        ):

            raise KeyError(
                f"Configured FX table "
                f"'{fx_table_name}' "
                f"was not loaded."
            )

        prepared_fx = (
            prepare_fx_rates(
                fx_df=base_tables[
                    fx_table_name
                ],
                rules=rules,
            )
        )

    # =====================================================
    # PHASE B + C
    # Currency then duplicate handling
    # =====================================================

    standardized_tables = {}

    for table_name, base_df in (
        base_tables.items()
    ):

        # -------------------------------------------------
        # Currency
        # -------------------------------------------------
        (
            working_df,
            currency_report,
        ) = standardize_currency_table(
            df=base_df,
            table_name=table_name,
            rules=rules,
            fx_rates=prepared_fx,
        )

        audit_reports[
            table_name
        ][
            "currency"
        ] = currency_report

        # -------------------------------------------------
        # Reviewed PK
        # -------------------------------------------------
        pk_columns = (
            resolve_primary_key_columns(
                schema_contract[
                    table_name
                ]
            )
        )

        # -------------------------------------------------
        # Duplicates
        # -------------------------------------------------
        (
            working_df,
            duplicate_report,
        ) = handle_duplicates(
            df=working_df,
            pk_columns=pk_columns,
        )

        audit_reports[
            table_name
        ][
            "duplicate"
        ] = duplicate_report

        standardized_tables[
            table_name
        ] = working_df

    return (
        standardized_tables,
        audit_reports,
    )