from pathlib import Path
import re
from typing import Dict

import pandas as pd
from sqlalchemy import text


# =========================================================
# REUSE REVIEW-PHASE DATABASE FUNCTIONS
# =========================================================
#
# Importing run_review.py does NOT execute its main() because
# that file is protected by:
#
# if __name__ == "__main__":
#     main()
#
# This keeps DB connection/read logic consistent between
# Review Phase and Build Phase.
# =========================================================

from run_review import (
    engine,
    validate_table_config,
    test_connection,
    read_all_bronze_tables,
)


from profiling.data_quality import (
    get_missing_masks,
)


from standardization.standardize_silver import (
    load_json_file,
    standardize_all_tables,
)


from validation.validate_silver import (
    validate_schema_contract,
    validate_all_tables,
    has_validation_failures,
)


# =========================================================
# 1. PROJECT PATHS
# =========================================================

BASE_DIR = (
    Path(__file__)
    .resolve()
    .parent
)

REVIEW_DIR = (
    BASE_DIR
    / "review"
)

REPORT_DIR = (
    BASE_DIR
    / "reports"
)

SCHEMA_REVIEW_PATH = (
    REVIEW_DIR
    / "silver_schema_review.json"
)

STANDARDIZATION_RULES_PATH = (
    BASE_DIR
    / "standardization"
    / "rules"
    / "standardization_rules.json"
)


# =========================================================
# 2. SQL OUTPUT SCHEMAS
# =========================================================

SILVER_SCHEMA = (
    "silver"
)

STAGING_SCHEMA = (
    "silver_staging"
)


# =========================================================
# 3. SQL IDENTIFIER VALIDATION
# =========================================================

def _validate_sql_identifier(
    name: str,
):
    """
    Validate schema/table identifiers before they are interpolated into SQL.

    Only letters, numbers and underscores are allowed and identifiers
    cannot begin with a number.
    """

    if not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*",
        str(name),
    ):

        raise ValueError(
            f"Unsafe SQL identifier: "
            f"{name}"
        )


# =========================================================
# 4. QUOTE SQL IDENTIFIER
# =========================================================

def _quote_identifier(
    name: str,
) -> str:
    """
    Validate and wrap one SQL Server identifier in square brackets.
    """

    _validate_sql_identifier(
        name
    )

    return (
        f"[{name}]"
    )


# =========================================================
# 5. CREATE SCHEMA IF NEEDED
# =========================================================

def ensure_sql_schema(
    connection,
    schema_name: str,
):
    """
    Create a SQL Server schema when it does not already exist.

    This function does not drop or alter existing schemas.
    """

    quoted_schema = (
        _quote_identifier(
            schema_name
        )
    )

    schema_literal = (
        schema_name.replace(
            "'",
            "''",
        )
    )

    connection.execute(
        text(
            f"""
            IF SCHEMA_ID(
                N'{schema_literal}'
            ) IS NULL
            BEGIN
                EXEC(
                    N'CREATE SCHEMA '
                    + N'{quoted_schema}'
                );
            END
            """
        )
    )


# =========================================================
# 6. RESOLVE SILVER TABLE NAME
# =========================================================

def get_silver_table_name(
    source_table_name: str,
    table_schema: Dict,
) -> str:
    """
    Resolve the physical Silver table name.

    Priority:
    1. table.silver_name from reviewed JSON, when supplied.
    2. Remove the conventional '_raw' suffix.
    3. Otherwise keep the source table name unchanged.

    Example:
        customers_raw -> customers
        departments   -> departments
    """

    table_metadata = (
        table_schema.get(
            "table",
            {},
        )
    )

    reviewed_name = (
        table_metadata.get(
            "silver_name"
        )
    )

    if reviewed_name:

        result = str(
            reviewed_name
        )

    elif source_table_name.endswith(
        "_raw"
    ):

        result = (
            source_table_name[
                :-4
            ]
        )

    else:

        result = (
            source_table_name
        )

    _validate_sql_identifier(
        result
    )

    return result


# =========================================================
# 7. BUILD SILVER TABLE NAME MAP
# =========================================================

def build_silver_table_name_map(
    schema_contract: Dict,
) -> Dict[str, str]:
    """
    Build source-table -> Silver-table mapping and reject collisions.

    Two Bronze tables are never allowed to overwrite the same
    physical Silver table.
    """

    table_map = {}

    used_names = set()

    for source_table_name, table_schema in (
        schema_contract.items()
    ):

        silver_table_name = (
            get_silver_table_name(
                source_table_name,
                table_schema,
            )
        )

        if silver_table_name in used_names:

            raise ValueError(
                "Duplicate Silver table name: "
                f"{silver_table_name}"
            )

        used_names.add(
            silver_table_name
        )

        table_map[
            source_table_name
        ] = silver_table_name

    return table_map


# =========================================================
# 8. COUNT REVIEWED MISSING VALUES
# =========================================================

def count_reviewed_missing_values(
    df: pd.DataFrame,
    table_schema: Dict,
    use_source_names: bool,
) -> int:
    """
    Count real NULL + configured fake-NULL values only for reviewed columns.

    Using reviewed columns keeps Before and After metrics comparable even
    when currency standardization adds new technical fields.
    """

    total = 0

    for source_column, column_info in (
        table_schema
        .get(
            "columns",
            {},
        )
        .items()
    ):

        if use_source_names:

            column_name = (
                source_column
            )

        else:

            column_name = (
                column_info.get(
                    "name",
                    source_column,
                )
            )

        if column_name not in df.columns:
            continue

        (
            real_null_mask,
            marker_null_mask,
        ) = get_missing_masks(
            df[
                column_name
            ]
        )

        total += int(
            (
                real_null_mask
                | marker_null_mask
            ).sum()
        )

    return total


# =========================================================
# 9. SUM AUDIT COLUMN
# =========================================================

def _sum_audit_column(
    report,
    column_name: str,
) -> int:
    """
    Safely sum one numeric column from an optional audit DataFrame.
    """

    if (
        report is None
        or report.empty
        or column_name
        not in report.columns
    ):
        return 0

    return int(
        pd.to_numeric(
            report[
                column_name
            ],
            errors="coerce",
        )
        .fillna(0)
        .sum()
    )


# =========================================================
# 10. TABLE HAS VALIDATION FAILURE
# =========================================================

def _table_failed(
    validation_report: pd.DataFrame,
    table_name: str,
) -> bool:
    """
    Return True when the specified table has any validation failure.
    """

    if validation_report.empty:
        return False

    return bool(
        (
            validation_report[
                "table_name"
            ]
            == table_name
        ).any()
    )


# =========================================================
# 11. INVALID-AFTER COUNT
# =========================================================

def _invalid_after_count(
    validation_report: pd.DataFrame,
    table_name: str,
) -> int:
    """
    Count remaining datatype/currency validation issues after standardization.

    NULL, duplicate and key problems are reported separately and therefore
    are intentionally excluded from this metric.
    """

    if validation_report.empty:
        return 0

    invalid_types = {
        "DATATYPE_CONVERSION",
        "RUNTIME_DATATYPE",
        "CURRENCY_MISSING_FX",
        "CURRENCY_INVALID_CODE",
    }

    rows = validation_report[
        (
            validation_report[
                "table_name"
            ]
            == table_name
        )
        & (
            validation_report[
                "check_type"
            ].isin(
                invalid_types
            )
        )
    ]

    return int(
        rows[
            "issue_count"
        ].sum()
    )


# =========================================================
# 12. BUILD BEFORE / AFTER REVIEW
# =========================================================

def build_before_after_review(
    bronze_tables: Dict[str, pd.DataFrame],
    standardized_tables: Dict[str, pd.DataFrame],
    schema_contract: Dict,
    audit_reports: Dict,
    validation_report: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build the final table-level Bronze vs Silver quality review.

    Output is intentionally concise and designed for Data Analyst review.
    """

    rows = []

    for table_name, bronze_df in (
        bronze_tables.items()
    ):

        silver_df = (
            standardized_tables[
                table_name
            ]
        )

        table_schema = (
            schema_contract[
                table_name
            ]
        )

        table_audit = (
            audit_reports[
                table_name
            ]
        )

        datatype_audit = (
            table_audit.get(
                "datatype"
            )
        )

        currency_audit = (
            table_audit.get(
                "currency"
            )
        )

        invalid_before = (
            _sum_audit_column(
                datatype_audit,
                "invalid_value_count",
            )
            + _sum_audit_column(
                currency_audit,
                "missing_fx_count",
            )
            + _sum_audit_column(
                currency_audit,
                "invalid_currency_count",
            )
        )

        rows.append(
            {
                "table_name":
                    table_name,

                "input_rows":
                    len(
                        bronze_df
                    ),

                "output_rows":
                    len(
                        silver_df
                    ),

                "changed_rows":
                    len(
                        table_audit.get(
                            "changed_row_indices",
                            [],
                        )
                    ),

                "duplicate_before":
                    int(
                        bronze_df
                        .duplicated(
                            keep=False
                        )
                        .sum()
                    ),

                "duplicate_after":
                    int(
                        silver_df
                        .duplicated(
                            keep=False
                        )
                        .sum()
                    ),

                "null_before":
                    count_reviewed_missing_values(
                        bronze_df,
                        table_schema,
                        use_source_names=True,
                    ),

                "null_after":
                    count_reviewed_missing_values(
                        silver_df,
                        table_schema,
                        use_source_names=False,
                    ),

                "invalid_before":
                    invalid_before,

                "invalid_after":
                    _invalid_after_count(
                        validation_report,
                        table_name,
                    ),

                "status":
                    (
                        "FAIL"
                        if _table_failed(
                            validation_report,
                            table_name,
                        )
                        else "PASS"
                    ),
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "table_name",
            "input_rows",
            "output_rows",
            "changed_rows",
            "duplicate_before",
            "duplicate_after",
            "null_before",
            "null_after",
            "invalid_before",
            "invalid_after",
            "status",
        ],
    )


# =========================================================
# 13. BUILD STANDARDIZATION SUMMARY
# =========================================================

def build_standardization_summary(
    bronze_tables: Dict[str, pd.DataFrame],
    standardized_tables: Dict[str, pd.DataFrame],
    audit_reports: Dict,
    validation_report: pd.DataFrame,
) -> pd.DataFrame:
    """
    Build the concise machine-generated Silver standardization summary.
    """

    rows = []

    for table_name, bronze_df in (
        bronze_tables.items()
    ):

        silver_df = (
            standardized_tables[
                table_name
            ]
        )

        table_audit = (
            audit_reports[
                table_name
            ]
        )

        duplicate_report = (
            table_audit.get(
                "duplicate"
            )
        )

        removed_duplicates = (
            _sum_audit_column(
                duplicate_report,
                "exact_duplicates_removed",
            )
        )

        rows.append(
            {
                "table_name":
                    table_name,

                "input_rows":
                    len(
                        bronze_df
                    ),

                "output_rows":
                    len(
                        silver_df
                    ),

                "changed_rows":
                    len(
                        table_audit.get(
                            "changed_row_indices",
                            [],
                        )
                    ),

                "removed_duplicates":
                    removed_duplicates,

                "status":
                    (
                        "FAIL"
                        if _table_failed(
                            validation_report,
                            table_name,
                        )
                        else "PASS"
                    ),
            }
        )

    return pd.DataFrame(
        rows,
        columns=[
            "table_name",
            "input_rows",
            "output_rows",
            "changed_rows",
            "removed_duplicates",
            "status",
        ],
    )


# =========================================================
# 14. SAVE BUILD REPORTS
# =========================================================

def save_build_reports(
    before_after_report: pd.DataFrame,
    standardization_summary: pd.DataFrame,
):
    """
    Save the two final Silver build reports.

    These files are written even when validation fails so the analyst
    can inspect why the build was blocked.
    """

    REVIEW_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    REPORT_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    before_after_path = (
        REVIEW_DIR
        / "silver_before_after_review.csv"
    )

    summary_path = (
        REPORT_DIR
        / "standardization_summary.csv"
    )

    before_after_report.to_csv(
        before_after_path,
        index=False,
        encoding="utf-8-sig",
    )

    standardization_summary.to_csv(
        summary_path,
        index=False,
        encoding="utf-8-sig",
    )

    return (
        before_after_path,
        summary_path,
    )


# =========================================================
# 15. PRINT VALIDATION FAILURES
# =========================================================

def print_validation_failures(
    validation_report: pd.DataFrame,
):
    """
    Print Silver validation failures in a readable terminal format.
    """

    if validation_report.empty:

        print(
            "Silver validation: PASS"
        )

        return

    print()

    print(
        "=" * 70
    )

    print(
        "SILVER VALIDATION FAILED"
    )

    print(
        "=" * 70
    )

    print()

    print(
        validation_report
        .to_string(
            index=False
        )
    )

    print()


# =========================================================
# 16. WRITE TABLES TO STAGING
# =========================================================

def write_staging_tables(
    standardized_tables: Dict[str, pd.DataFrame],
    table_name_map: Dict[str, str],
):
    """
    Write every validated in-memory Silver DataFrame to silver_staging.

    The production Silver schema is not touched during this step.
    """

    with engine.begin() as connection:

        ensure_sql_schema(
            connection,
            STAGING_SCHEMA,
        )

        for (
            source_table_name,
            df,
        ) in standardized_tables.items():

            target_table_name = (
                table_name_map[
                    source_table_name
                ]
            )

            print(
                f"Staging "
                f"{STAGING_SCHEMA}."
                f"{target_table_name}"
            )

            df.to_sql(
                name=target_table_name,
                con=connection,
                schema=STAGING_SCHEMA,
                if_exists="replace",
                index=False,
            )


# =========================================================
# 17. PUBLISH STAGING TO SILVER
# =========================================================

def publish_staging_tables(
    standardized_tables: Dict[str, pd.DataFrame],
    table_name_map: Dict[str, str],
):
    """
    Publish all staging tables to the final Silver schema.

    Publication runs inside one SQLAlchemy transaction:

    1. Create silver schema when needed.
    2. Replace each target Silver table from staging.
    3. Verify row counts.
    4. Drop staging tables only after successful verification.

    If publication raises an exception, the transaction is rolled back.
    """

    with engine.begin() as connection:

        ensure_sql_schema(
            connection,
            SILVER_SCHEMA,
        )

        # -------------------------------------------------
        # Publish all tables
        # -------------------------------------------------

        for (
            source_table_name,
            df,
        ) in standardized_tables.items():

            target_table_name = (
                table_name_map[
                    source_table_name
                ]
            )

            silver_schema_sql = (
                _quote_identifier(
                    SILVER_SCHEMA
                )
            )

            staging_schema_sql = (
                _quote_identifier(
                    STAGING_SCHEMA
                )
            )

            table_sql = (
                _quote_identifier(
                    target_table_name
                )
            )

            silver_object = (
                f"{SILVER_SCHEMA}."
                f"{target_table_name}"
            )

            silver_object_literal = (
                silver_object.replace(
                    "'",
                    "''",
                )
            )

            # Remove the current table only inside the publish
            # transaction. A later exception causes rollback.
            connection.execute(
                text(
                    f"""
                    IF OBJECT_ID(
                        N'{silver_object_literal}',
                        N'U'
                    ) IS NOT NULL
                    BEGIN
                        DROP TABLE
                        {silver_schema_sql}.
                        {table_sql};
                    END
                    """
                )
            )

            connection.execute(
                text(
                    f"""
                    SELECT *
                    INTO
                        {silver_schema_sql}.
                        {table_sql}
                    FROM
                        {staging_schema_sql}.
                        {table_sql};
                    """
                )
            )

            # ---------------------------------------------
            # Row-count verification before commit.
            # ---------------------------------------------

            sql_row_count = (
                connection.execute(
                    text(
                        f"""
                        SELECT COUNT(*)
                        FROM
                            {silver_schema_sql}.
                            {table_sql}
                        """
                    )
                )
                .scalar_one()
            )

            expected_count = (
                len(df)
            )

            if int(
                sql_row_count
            ) != expected_count:

                raise RuntimeError(
                    f"Row-count verification "
                    f"failed for "
                    f"{SILVER_SCHEMA}."
                    f"{target_table_name}: "
                    f"expected "
                    f"{expected_count}, "
                    f"got "
                    f"{sql_row_count}."
                )

        # -------------------------------------------------
        # Only clear staging after every Silver table passed.
        # -------------------------------------------------

        for (
            source_table_name,
            _
        ) in standardized_tables.items():

            target_table_name = (
                table_name_map[
                    source_table_name
                ]
            )

            staging_schema_sql = (
                _quote_identifier(
                    STAGING_SCHEMA
                )
            )

            table_sql = (
                _quote_identifier(
                    target_table_name
                )
            )

            connection.execute(
                text(
                    f"""
                    DROP TABLE
                        {staging_schema_sql}.
                        {table_sql};
                    """
                )
            )


# =========================================================
# 18. WRITE SILVER SAFELY
# =========================================================

def write_silver_tables_safely(
    standardized_tables: Dict[str, pd.DataFrame],
    schema_contract: Dict,
):
    """
    Publish validated Silver data through a staging-first workflow.

    Silver production tables are never written directly from pandas.
    """

    table_name_map = (
        build_silver_table_name_map(
            schema_contract
        )
    )

    # First write everything to a non-production staging schema.
    write_staging_tables(
        standardized_tables,
        table_name_map,
    )

    # Only after successful staging do we replace Silver tables.
    publish_staging_tables(
        standardized_tables,
        table_name_map,
    )

    return table_name_map


# =========================================================
# 19. PRINT SUCCESS
# =========================================================

def print_build_success(
    table_name_map: Dict[str, str],
    before_after_path: Path,
    summary_path: Path,
):
    """
    Print successful Silver build outputs.
    """

    print()

    print(
        "=" * 70
    )

    print(
        "SILVER LAYER COMPLETED"
    )

    print(
        "=" * 70
    )

    print()

    print(
        "SILVER TABLES:"
    )

    for silver_table_name in (
        table_name_map.values()
    ):

        print(
            f"- {SILVER_SCHEMA}."
            f"{silver_table_name}"
        )

    print()

    print(
        "REPORTS:"
    )

    print(
        f"- reports/"
        f"{summary_path.name}"
    )

    print(
        f"- review/"
        f"{before_after_path.name}"
    )


# =========================================================
# 20. MAIN BUILD
# =========================================================

def main():
    """
    Build the complete Silver Layer from the manually reviewed schema.

    Flow:
        1. Validate configuration and SQL connection.
        2. Load reviewed Silver schema + standardization rules.
        3. Load Bronze tables.
        4. Validate the reviewed schema contract.
        5. Standardize all tables in memory.
        6. Validate standardized Silver data.
        7. Generate Before/After reports.
        8. STOP if validation fails.
        9. Stage all validated tables.
        10. Publish to SQL Server silver schema.

    Bronze is never overwritten.
    """

    print(
        "=" * 70
    )

    print(
        "BUILD SILVER LAYER"
    )

    print(
        "=" * 70
    )

    # =====================================================
    # 1. REQUIRED FILES
    # =====================================================

    if not SCHEMA_REVIEW_PATH.exists():

        raise FileNotFoundError(
            "Missing reviewed schema: "
            f"{SCHEMA_REVIEW_PATH}"
        )

    if not STANDARDIZATION_RULES_PATH.exists():

        raise FileNotFoundError(
            "Missing standardization rules: "
            f"{STANDARDIZATION_RULES_PATH}"
        )

    # =====================================================
    # 2. DATABASE CONFIG / CONNECTION
    # =====================================================

    validate_table_config()

    test_connection()

    # =====================================================
    # 3. LOAD BRONZE
    # =====================================================

    print()

    print(
        "Loading Bronze tables..."
    )

    bronze_tables = (
        read_all_bronze_tables()
    )

    # =====================================================
    # 4. LOAD REVIEWED CONTRACT + RULES
    # =====================================================

    schema_contract = (
        load_json_file(
            SCHEMA_REVIEW_PATH
        )
    )

    rules = (
        load_json_file(
            STANDARDIZATION_RULES_PATH
        )
    )

    # =====================================================
    # 5. PRE-BUILD CONTRACT VALIDATION
    # =====================================================

    print()

    print(
        "Validating reviewed schema..."
    )

    contract_failures = (
        validate_schema_contract(
            bronze_tables,
            schema_contract,
        )
    )

    if not contract_failures.empty:

        print_validation_failures(
            contract_failures
        )

        raise RuntimeError(
            "Silver schema review is incomplete. "
            "No Silver data was written."
        )

    print(
        "Schema contract: PASS"
    )

    # =====================================================
    # 6. STANDARDIZATION
    # =====================================================

    print()

    print(
        "Standardizing Bronze data..."
    )

    (
        standardized_tables,
        audit_reports,
    ) = standardize_all_tables(
        all_tables=bronze_tables,
        schema_contract=schema_contract,
        rules=rules,
    )

    # =====================================================
    # 7. POST-STANDARDIZATION VALIDATION
    # =====================================================

    print()

    print(
        "Validating standardized data..."
    )

    validation_report = (
        validate_all_tables(
            standardized_tables=(
                standardized_tables
            ),
            schema_contract=(
                schema_contract
            ),
            audit_reports=(
                audit_reports
            ),
        )
    )

    # =====================================================
    # 8. FINAL REVIEW REPORTS
    # =====================================================

    before_after_report = (
        build_before_after_review(
            bronze_tables=bronze_tables,
            standardized_tables=(
                standardized_tables
            ),
            schema_contract=(
                schema_contract
            ),
            audit_reports=(
                audit_reports
            ),
            validation_report=(
                validation_report
            ),
        )
    )

    standardization_summary = (
        build_standardization_summary(
            bronze_tables=bronze_tables,
            standardized_tables=(
                standardized_tables
            ),
            audit_reports=(
                audit_reports
            ),
            validation_report=(
                validation_report
            ),
        )
    )

    (
        before_after_path,
        summary_path,
    ) = save_build_reports(
        before_after_report,
        standardization_summary,
    )

    # =====================================================
    # 9. BLOCK WRITE WHEN VALIDATION FAILS
    # =====================================================

    if has_validation_failures(
        validation_report
    ):

        print_validation_failures(
            validation_report
        )

        print(
            "Before/After review was generated."
        )

        print(
            "NO SILVER TABLE WAS WRITTEN."
        )

        raise RuntimeError(
            "Silver validation failed."
        )

    print(
        "Silver validation: PASS"
    )

    # =====================================================
    # 10. WRITE SILVER
    # =====================================================

    print()

    print(
        "Writing validated Silver tables..."
    )

    table_name_map = (
        write_silver_tables_safely(
            standardized_tables=(
                standardized_tables
            ),
            schema_contract=(
                schema_contract
            ),
        )
    )

    # =====================================================
    # 11. SUCCESS
    # =====================================================

    print_build_success(
        table_name_map=(
            table_name_map
        ),
        before_after_path=(
            before_after_path
        ),
        summary_path=(
            summary_path
        ),
    )


if __name__ == "__main__":
    main()