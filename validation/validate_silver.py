from datetime import date, datetime
from decimal import Decimal
from numbers import Integral
from typing import Dict, List, Optional, Tuple

import pandas as pd


# =========================================================
# SUPPORTED SILVER DATATYPES
# =========================================================

SUPPORTED_DATATYPES = {
    "string",
    "integer",
    "decimal",
    "date",
    "datetime",
    "boolean",
}


# =========================================================
# 1. NORMALIZE DATATYPE NAME
# =========================================================

def _normalize_type_name(
    target_type: str,
) -> str:
    """
    Normalize reviewed datatype aliases to the canonical Silver types.

    This mirrors the datatype standardization engine so validation and
    transformation interpret schema datatypes consistently.
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

        "timestamp": "datetime",
    }

    return aliases.get(
        value,
        value,
    )


# =========================================================
# 2. BUILD ONE FAILURE ROW
# =========================================================

def _failure(
    table_name: str,
    check_type: str,
    column_name: str,
    issue_count: int,
    detail: str,
) -> Dict:
    """
    Build one standardized Silver validation failure record.

    Using one shared structure keeps validation output easy to
    aggregate later into table-level PASS / FAIL results.
    """

    return {
        "table_name":
            table_name,

        "check_type":
            check_type,

        "column_name":
            column_name,

        "issue_count":
            int(issue_count),

        "status":
            "FAIL",

        "detail":
            detail,
    }


# =========================================================
# 3. RESOLVE REVIEWED COLUMN NAME
# =========================================================

def resolve_reviewed_column_name(
    table_schema: Dict,
    column_name: str,
) -> Optional[str]:
    """
    Resolve either a Bronze/source column name or an already-reviewed
    Silver column name to the final Silver column name.

    Returns None when the column does not exist in the reviewed schema.
    """

    columns = table_schema.get(
        "columns",
        {},
    )

    # Source/Bronze name.
    if column_name in columns:

        return (
            columns[
                column_name
            ].get(
                "name",
                column_name,
            )
        )

    # Already a Silver name.
    for (
        source_column,
        column_info,
    ) in columns.items():

        silver_name = (
            column_info.get(
                "name",
                source_column,
            )
        )

        if silver_name == column_name:
            return silver_name

    return None


# =========================================================
# 4. PARSE FK REFERENCE
# =========================================================

def _parse_fk_reference(
    reference: str,
) -> Tuple[str, str]:
    """
    Parse a reviewed FK reference in the form:

        parent_table.parent_column

    Invalid reference formats raise ValueError.
    """

    parts = (
        str(reference)
        .split(
            ".",
            1,
        )
    )

    if (
        len(parts) != 2
        or not parts[0]
        or not parts[1]
    ):

        raise ValueError(
            "FK reference must use "
            "'table.column' format: "
            f"{reference}"
        )

    return (
        parts[0],
        parts[1],
    )


# =========================================================
# 5. VALIDATE REVIEWED SCHEMA CONTRACT
# =========================================================

def validate_schema_contract(
    bronze_tables: Dict[str, pd.DataFrame],
    schema_contract: Dict,
) -> pd.DataFrame:
    """
    Validate silver_schema_review.json before any transformation occurs.

    Checks:
    - every loaded Bronze table exists in the contract,
    - every reviewed table exists in Bronze,
    - every Bronze column is reviewed,
    - every reviewed source column exists,
    - target Silver column names are unique,
    - datatype names are supported,
    - nullable is a real boolean,
    - every table has an approved PK unless explicitly allowed not to,
    - PK columns exist,
    - FK columns and referenced parents exist.

    The function never modifies Bronze data.
    """

    failures = []

    bronze_names = set(
        bronze_tables.keys()
    )

    contract_names = set(
        schema_contract.keys()
    )

    # -----------------------------------------------------
    # Missing contract tables
    # -----------------------------------------------------

    for table_name in sorted(
        bronze_names
        - contract_names
    ):

        failures.append(
            _failure(
                table_name=table_name,
                check_type="SCHEMA_CONTRACT",
                column_name="",
                issue_count=1,
                detail=(
                    "Bronze table is missing "
                    "from silver_schema_review.json."
                ),
            )
        )

    # -----------------------------------------------------
    # Contract table not loaded
    # -----------------------------------------------------

    for table_name in sorted(
        contract_names
        - bronze_names
    ):

        failures.append(
            _failure(
                table_name=table_name,
                check_type="SCHEMA_CONTRACT",
                column_name="",
                issue_count=1,
                detail=(
                    "Reviewed table does not "
                    "exist in loaded Bronze data."
                ),
            )
        )

    # -----------------------------------------------------
    # Validate each table
    # -----------------------------------------------------

    for table_name in sorted(
        bronze_names
        & contract_names
    ):

        df = bronze_tables[
            table_name
        ]

        table_schema = (
            schema_contract[
                table_name
            ]
        )

        table_metadata = (
            table_schema.get(
                "table",
                {},
            )
        )

        columns = (
            table_schema.get(
                "columns",
                {},
            )
        )

        bronze_columns = set(
            df.columns
        )

        reviewed_source_columns = set(
            columns.keys()
        )

        # ---------------------------------------------
        # Every Bronze column must be reviewed.
        # ---------------------------------------------

        for column_name in sorted(
            bronze_columns
            - reviewed_source_columns
        ):

            failures.append(
                _failure(
                    table_name,
                    "SCHEMA_CONTRACT",
                    column_name,
                    1,
                    (
                        "Bronze column is not "
                        "defined in reviewed schema."
                    ),
                )
            )

        # ---------------------------------------------
        # Reviewed source columns must exist.
        # ---------------------------------------------

        for column_name in sorted(
            reviewed_source_columns
            - bronze_columns
        ):

            failures.append(
                _failure(
                    table_name,
                    "SCHEMA_CONTRACT",
                    column_name,
                    1,
                    (
                        "Reviewed source column "
                        "does not exist in Bronze."
                    ),
                )
            )

        # ---------------------------------------------
        # Column metadata
        # ---------------------------------------------

        target_names = []

        for (
            source_column,
            column_info,
        ) in columns.items():

            target_name = (
                column_info.get(
                    "name",
                    source_column,
                )
            )

            target_names.append(
                target_name
            )

            datatype = (
                _normalize_type_name(
                    column_info.get(
                        "datatype",
                        "",
                    )
                )
            )

            if datatype not in SUPPORTED_DATATYPES:

                failures.append(
                    _failure(
                        table_name,
                        "SCHEMA_CONTRACT",
                        target_name,
                        1,
                        (
                            "Unsupported datatype: "
                            f"{column_info.get('datatype')}"
                        ),
                    )
                )

            nullable = (
                column_info.get(
                    "nullable"
                )
            )

            if not isinstance(
                nullable,
                bool,
            ):

                failures.append(
                    _failure(
                        table_name,
                        "SCHEMA_CONTRACT",
                        target_name,
                        1,
                        (
                            "'nullable' must be "
                            "true or false."
                        ),
                    )
                )

        # ---------------------------------------------
        # Target names must be unique.
        # ---------------------------------------------

        if (
            len(target_names)
            != len(set(target_names))
        ):

            failures.append(
                _failure(
                    table_name,
                    "SCHEMA_CONTRACT",
                    "",
                    1,
                    (
                        "Two or more reviewed "
                        "columns use the same "
                        "Silver column name."
                    ),
                )
            )

        # ---------------------------------------------
        # PK
        # ---------------------------------------------

        primary_key = (
            table_metadata.get(
                "primary_key",
                [],
            )
        )

        allow_no_pk = bool(
            table_metadata.get(
                "allow_no_primary_key",
                False,
            )
        )

        if (
            not primary_key
            and not allow_no_pk
        ):

            failures.append(
                _failure(
                    table_name,
                    "SCHEMA_CONTRACT",
                    "",
                    1,
                    (
                        "No approved primary key. "
                        "Review table grain/PK "
                        "before building Silver."
                    ),
                )
            )

        for pk_column in primary_key:

            resolved = (
                resolve_reviewed_column_name(
                    table_schema,
                    pk_column,
                )
            )

            if resolved is None:

                failures.append(
                    _failure(
                        table_name,
                        "SCHEMA_CONTRACT",
                        pk_column,
                        1,
                        (
                            "Primary-key column "
                            "does not exist in "
                            "reviewed schema."
                        ),
                    )
                )

        # ---------------------------------------------
        # FK
        # ---------------------------------------------

        foreign_keys = (
            table_metadata.get(
                "foreign_keys",
                [],
            )
        )

        for fk in foreign_keys:

            child_column = (
                fk.get(
                    "column",
                    "",
                )
            )

            reference = (
                fk.get(
                    "references",
                    "",
                )
            )

            child_resolved = (
                resolve_reviewed_column_name(
                    table_schema,
                    child_column,
                )
            )

            if child_resolved is None:

                failures.append(
                    _failure(
                        table_name,
                        "SCHEMA_CONTRACT",
                        child_column,
                        1,
                        (
                            "FK child column does "
                            "not exist."
                        ),
                    )
                )

                continue

            try:

                (
                    parent_table,
                    parent_column,
                ) = _parse_fk_reference(
                    reference
                )

            except ValueError as error:

                failures.append(
                    _failure(
                        table_name,
                        "SCHEMA_CONTRACT",
                        child_column,
                        1,
                        str(error),
                    )
                )

                continue

            if parent_table not in schema_contract:

                failures.append(
                    _failure(
                        table_name,
                        "SCHEMA_CONTRACT",
                        child_column,
                        1,
                        (
                            "FK parent table "
                            f"'{parent_table}' "
                            "does not exist."
                        ),
                    )
                )

                continue

            parent_resolved = (
                resolve_reviewed_column_name(
                    schema_contract[
                        parent_table
                    ],
                    parent_column,
                )
            )

            if parent_resolved is None:

                failures.append(
                    _failure(
                        table_name,
                        "SCHEMA_CONTRACT",
                        child_column,
                        1,
                        (
                            "Referenced parent "
                            f"column '{reference}' "
                            "does not exist."
                        ),
                    )
                )

    return pd.DataFrame(
        failures,
        columns=[
            "table_name",
            "check_type",
            "column_name",
            "issue_count",
            "status",
            "detail",
        ],
    )


# =========================================================
# 6. VALIDATE REQUIRED COLUMNS
# =========================================================

def validate_required_columns(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
) -> List[Dict]:
    """
    Check that every reviewed Silver column exists after standardization.

    Extra generated columns such as currency conversion fields are allowed.
    """

    failures = []

    for (
        source_column,
        column_info,
    ) in (
        table_schema
        .get(
            "columns",
            {},
        )
        .items()
    ):

        silver_column = (
            column_info.get(
                "name",
                source_column,
            )
        )

        if silver_column not in df.columns:

            failures.append(
                _failure(
                    table_name,
                    "REQUIRED_COLUMN",
                    silver_column,
                    1,
                    "Required Silver column is missing.",
                )
            )

    return failures


# =========================================================
# 7. VALIDATE NULLABILITY
# =========================================================

def validate_nullability(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
) -> List[Dict]:
    """
    Check reviewed non-nullable columns for missing values.
    """

    failures = []

    for (
        source_column,
        column_info,
    ) in (
        table_schema
        .get(
            "columns",
            {},
        )
        .items()
    ):

        if column_info.get(
            "nullable",
            True,
        ):
            continue

        silver_column = (
            column_info.get(
                "name",
                source_column,
            )
        )

        if silver_column not in df.columns:
            continue

        null_count = int(
            df[
                silver_column
            ].isna().sum()
        )

        if null_count > 0:

            failures.append(
                _failure(
                    table_name,
                    "NULLABILITY",
                    silver_column,
                    null_count,
                    (
                        "Reviewed nullable=false "
                        "but NULL values remain."
                    ),
                )
            )

    return failures


# =========================================================
# 8. CHECK RUNTIME DATATYPE
# =========================================================

def _series_matches_type(
    series: pd.Series,
    target_type: str,
) -> bool:
    """
    Check whether non-null values match the reviewed Silver datatype.

    This is a post-transformation safety check.
    """

    canonical_type = (
        _normalize_type_name(
            target_type
        )
    )

    non_null = (
        series.dropna()
    )

    if non_null.empty:
        return True

    if canonical_type == "string":

        return bool(
            non_null.map(
                lambda value:
                isinstance(
                    value,
                    str,
                )
            ).all()
        )

    if canonical_type == "integer":

        return bool(
            non_null.map(
                lambda value:
                (
                    isinstance(
                        value,
                        Integral,
                    )
                    and not isinstance(
                        value,
                        bool,
                    )
                )
            ).all()
        )

    if canonical_type == "decimal":

        return bool(
            non_null.map(
                lambda value:
                isinstance(
                    value,
                    Decimal,
                )
            ).all()
        )

    if canonical_type == "date":

        return bool(
            non_null.map(
                lambda value:
                (
                    isinstance(
                        value,
                        date,
                    )
                    and not isinstance(
                        value,
                        datetime,
                    )
                )
            ).all()
        )

    if canonical_type == "datetime":

        return bool(
            non_null.map(
                lambda value:
                isinstance(
                    value,
                    (
                        datetime,
                        pd.Timestamp,
                    ),
                )
            ).all()
        )

    if canonical_type == "boolean":

        return bool(
            pd.api.types.is_bool_dtype(
                series.dtype
            )
        )

    return False


# =========================================================
# 9. VALIDATE DATATYPES
# =========================================================

def validate_runtime_datatypes(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
) -> List[Dict]:
    """
    Verify that standardized column values match reviewed datatypes.
    """

    failures = []

    for (
        source_column,
        column_info,
    ) in (
        table_schema
        .get(
            "columns",
            {},
        )
        .items()
    ):

        silver_column = (
            column_info.get(
                "name",
                source_column,
            )
        )

        if silver_column not in df.columns:
            continue

        target_type = (
            column_info.get(
                "datatype",
                "string",
            )
        )

        if not _series_matches_type(
            df[
                silver_column
            ],
            target_type,
        ):

            failures.append(
                _failure(
                    table_name,
                    "RUNTIME_DATATYPE",
                    silver_column,
                    1,
                    (
                        "Standardized values do "
                        "not match reviewed type "
                        f"'{target_type}'."
                    ),
                )
            )

    return failures


# =========================================================
# 10. DATATYPE CONVERSION FAILURES
# =========================================================

def validate_datatype_conversion_audit(
    table_name: str,
    table_audit: Dict,
) -> List[Dict]:
    """
    Fail when source values could not be converted to the reviewed datatype.

    This check is necessary even for nullable columns because an invalid
    value must not silently become an accepted NULL.
    """

    failures = []

    report = (
        table_audit.get(
            "datatype"
        )
    )

    if (
        report is None
        or report.empty
    ):
        return failures

    for _, row in report.iterrows():

        invalid_count = int(
            row.get(
                "invalid_value_count",
                0,
            )
        )

        if invalid_count == 0:
            continue

        failures.append(
            _failure(
                table_name,
                "DATATYPE_CONVERSION",
                row[
                    "column_name"
                ],
                invalid_count,
                (
                    "Values could not be "
                    "converted. Examples: "
                    + str(
                        row.get(
                            "invalid_examples",
                            "",
                        )
                    )
                ),
            )
        )

    return failures


# =========================================================
# 11. VALIDATE PRIMARY KEY
# =========================================================

def validate_primary_key(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
) -> List[Dict]:
    """
    Validate the reviewed primary key.

    A valid Silver PK must:
    - exist,
    - contain no NULL,
    - be unique across its full composite key.
    """

    failures = []

    table_metadata = (
        table_schema.get(
            "table",
            {},
        )
    )

    primary_key = (
        table_metadata.get(
            "primary_key",
            [],
        )
    )

    allow_no_pk = bool(
        table_metadata.get(
            "allow_no_primary_key",
            False,
        )
    )

    if not primary_key:

        if not allow_no_pk:

            failures.append(
                _failure(
                    table_name,
                    "PRIMARY_KEY",
                    "",
                    1,
                    "No approved primary key.",
                )
            )

        return failures

    resolved_pk = []

    for pk_column in primary_key:

        silver_column = (
            resolve_reviewed_column_name(
                table_schema,
                pk_column,
            )
        )

        if (
            silver_column is None
            or silver_column not in df.columns
        ):

            failures.append(
                _failure(
                    table_name,
                    "PRIMARY_KEY",
                    pk_column,
                    1,
                    "PK column is missing.",
                )
            )

            continue

        resolved_pk.append(
            silver_column
        )

    if len(resolved_pk) != len(
        primary_key
    ):
        return failures

    null_pk_mask = (
        df[
            resolved_pk
        ]
        .isna()
        .any(
            axis=1
        )
    )

    null_count = int(
        null_pk_mask.sum()
    )

    if null_count > 0:

        failures.append(
            _failure(
                table_name,
                "PK_NULL",
                " + ".join(
                    resolved_pk
                ),
                null_count,
                "Primary-key rows contain NULL.",
            )
        )

    duplicate_mask = (
        df.duplicated(
            subset=resolved_pk,
            keep=False,
        )
    )

    duplicate_count = int(
        duplicate_mask.sum()
    )

    if duplicate_count > 0:

        failures.append(
            _failure(
                table_name,
                "PK_DUPLICATE",
                " + ".join(
                    resolved_pk
                ),
                duplicate_count,
                (
                    "Conflicting primary-key "
                    "duplicates remain."
                ),
            )
        )

    return failures


# =========================================================
# 12. VALIDATE EXACT DUPLICATES
# =========================================================

def validate_exact_duplicates(
    table_name: str,
    df: pd.DataFrame,
) -> List[Dict]:
    """
    Confirm that exact duplicate rows were removed.
    """

    duplicate_count = int(
        df.duplicated(
            keep=False
        ).sum()
    )

    if duplicate_count == 0:
        return []

    return [
        _failure(
            table_name,
            "EXACT_DUPLICATE",
            "",
            duplicate_count,
            "Exact duplicate rows remain.",
        )
    ]


# =========================================================
# 13. VALIDATE FOREIGN KEYS
# =========================================================

def validate_foreign_keys(
    table_name: str,
    standardized_tables: Dict[str, pd.DataFrame],
    schema_contract: Dict,
) -> List[Dict]:
    """
    Validate reviewed FK relationships using standardized Silver values.

    NULL child values are ignored here and are handled by nullable rules.
    """

    failures = []

    child_df = (
        standardized_tables[
            table_name
        ]
    )

    child_schema = (
        schema_contract[
            table_name
        ]
    )

    foreign_keys = (
        child_schema
        .get(
            "table",
            {},
        )
        .get(
            "foreign_keys",
            [],
        )
    )

    for fk in foreign_keys:

        child_source_column = (
            fk[
                "column"
            ]
        )

        (
            parent_table,
            parent_source_column,
        ) = _parse_fk_reference(
            fk[
                "references"
            ]
        )

        child_column = (
            resolve_reviewed_column_name(
                child_schema,
                child_source_column,
            )
        )

        parent_schema = (
            schema_contract[
                parent_table
            ]
        )

        parent_column = (
            resolve_reviewed_column_name(
                parent_schema,
                parent_source_column,
            )
        )

        if (
            child_column not in child_df.columns
            or parent_column is None
        ):
            continue

        parent_df = (
            standardized_tables[
                parent_table
            ]
        )

        if parent_column not in parent_df.columns:
            continue

        # FK targets should be unique.
        parent_duplicate_count = int(
            parent_df[
                parent_column
            ]
            .dropna()
            .duplicated(
                keep=False
            )
            .sum()
        )

        if parent_duplicate_count > 0:

            failures.append(
                _failure(
                    table_name,
                    "FK_PARENT_DUPLICATE",
                    child_column,
                    parent_duplicate_count,
                    (
                        "Referenced parent column "
                        f"{parent_table}."
                        f"{parent_column} "
                        "is not unique."
                    ),
                )
            )

        parent_values = set(
            parent_df[
                parent_column
            ]
            .dropna()
            .tolist()
        )

        child_values = (
            child_df[
                child_column
            ]
        )

        non_null_mask = (
            child_values.notna()
        )

        orphan_mask = (
            non_null_mask
            & ~child_values.isin(
                parent_values
            )
        )

        orphan_count = int(
            orphan_mask.sum()
        )

        if orphan_count > 0:

            failures.append(
                _failure(
                    table_name,
                    "FK_ORPHAN",
                    child_column,
                    orphan_count,
                    (
                        "Child values do not "
                        "exist in "
                        f"{parent_table}."
                        f"{parent_column}."
                    ),
                )
            )

    return failures


# =========================================================
# 14. VALIDATE CURRENCY
# =========================================================

def validate_currency_audit(
    table_name: str,
    table_audit: Dict,
) -> List[Dict]:
    """
    Fail when configured currency conversion has missing FX rates
    or invalid currency codes.
    """

    failures = []

    report = (
        table_audit.get(
            "currency"
        )
    )

    if (
        report is None
        or report.empty
    ):
        return failures

    row = (
        report.iloc[0]
    )

    missing_fx = int(
        row.get(
            "missing_fx_count",
            0,
        )
    )

    invalid_currency = int(
        row.get(
            "invalid_currency_count",
            0,
        )
    )

    if missing_fx > 0:

        failures.append(
            _failure(
                table_name,
                "CURRENCY_MISSING_FX",
                "",
                missing_fx,
                (
                    "Foreign-currency rows "
                    "could not find an approved "
                    "FX rate."
                ),
            )
        )

    if invalid_currency > 0:

        failures.append(
            _failure(
                table_name,
                "CURRENCY_INVALID_CODE",
                "",
                invalid_currency,
                (
                    "Currency codes are not "
                    "available in the approved "
                    "FX data."
                ),
            )
        )

    return failures


# =========================================================
# 15. VALIDATE ALL SILVER TABLES
# =========================================================

def validate_all_tables(
    standardized_tables: Dict[str, pd.DataFrame],
    schema_contract: Dict,
    audit_reports: Dict[str, Dict],
) -> pd.DataFrame:
    """
    Run all post-standardization Silver validation checks.

    Returned DataFrame contains failures only.
    An empty DataFrame means all configured checks passed.
    """

    failures = []

    for table_name, df in (
        standardized_tables.items()
    ):

        table_schema = (
            schema_contract[
                table_name
            ]
        )

        table_audit = (
            audit_reports.get(
                table_name,
                {},
            )
        )

        failures.extend(
            validate_required_columns(
                table_name,
                df,
                table_schema,
            )
        )

        failures.extend(
            validate_nullability(
                table_name,
                df,
                table_schema,
            )
        )

        failures.extend(
            validate_runtime_datatypes(
                table_name,
                df,
                table_schema,
            )
        )

        failures.extend(
            validate_datatype_conversion_audit(
                table_name,
                table_audit,
            )
        )

        failures.extend(
            validate_primary_key(
                table_name,
                df,
                table_schema,
            )
        )

        failures.extend(
            validate_exact_duplicates(
                table_name,
                df,
            )
        )

        failures.extend(
            validate_currency_audit(
                table_name,
                table_audit,
            )
        )

    # FK validation needs all tables to exist first.
    for table_name in (
        standardized_tables
    ):

        failures.extend(
            validate_foreign_keys(
                table_name,
                standardized_tables,
                schema_contract,
            )
        )

    return pd.DataFrame(
        failures,
        columns=[
            "table_name",
            "check_type",
            "column_name",
            "issue_count",
            "status",
            "detail",
        ],
    )


# =========================================================
# 16. FAILURE FLAG
# =========================================================

def has_validation_failures(
    validation_report: pd.DataFrame,
) -> bool:
    """
    Return True when at least one critical Silver validation failed.
    """

    return (
        not validation_report.empty
    )