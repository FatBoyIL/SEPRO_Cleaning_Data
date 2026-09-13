from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text


from config.table_config import (
    BRONZE_TABLES,
    EXPECTED_TABLE_COUNT,
    SERVER,
    DATABASE,
)

from data_quality import (
    profile_table,
    profile_columns,
    check_missing_values,
    check_primary_key,
    check_exact_duplicates,
    check_text_variants,
    check_whitespace_issues,
    check_column_structure,
    suggest_datatypes,
    check_datatype_issues,
)

from schema_proposal import (
    build_schema_proposal,
    export_schema_proposal,
)


# =========================================================
# 1. PROJECT PATH
# =========================================================

# Keep each Phase 1 output category in its own destination so profiling,
# Data Quality, schema proposals, and manual review remain easy to locate.

BASE_DIR = Path(
    __file__
).resolve().parent

REPORT_DIR = (
    BASE_DIR
    / "reports"
)

PROFILE_DIR = (
    REPORT_DIR
    / "01_profile"
)

DQ_DIR = (
    REPORT_DIR
    / "02_data_quality"
)

PROPOSAL_DIR = (
    REPORT_DIR
    / "03_schema_proposal"
)

REVIEW_DIR = (
    BASE_DIR
    / "review"
)

for folder in (
    REPORT_DIR,
    PROFILE_DIR,
    DQ_DIR,
    PROPOSAL_DIR,
    REVIEW_DIR,
):
    folder.mkdir(
        parents=True,
        exist_ok=True,
    )


# =========================================================
# 2. SQL SERVER CONNECTION
# =========================================================

# A single SQLAlchemy engine gives every Bronze read the same connection
# configuration and avoids duplicating database setup across the pipeline.

connection_string = quote_plus(
    f"DRIVER={{ODBC Driver 18 for SQL Server}};"
    f"SERVER={SERVER};"
    f"DATABASE={DATABASE};"
    f"Trusted_Connection=yes;"
    f"TrustServerCertificate=yes;"
)

engine = create_engine(
    f"mssql+pyodbc:///?"
    f"odbc_connect={connection_string}"
)


# =========================================================
# 3. CHECK CONFIG
# =========================================================

def validate_table_config():
    """
    Fail early if the configured Bronze inventory is incomplete or duplicated.
    The rest of the pipeline assumes this list represents the full source set.
    """
    actual_count = len(
        BRONZE_TABLES
    )

    if (
        actual_count
        != EXPECTED_TABLE_COUNT
    ):
        raise ValueError(
            f"Expected "
            f"{EXPECTED_TABLE_COUNT} "
            f"Bronze tables, "
            f"but found "
            f"{actual_count}."
        )

    if (
        len(BRONZE_TABLES)
        != len(set(BRONZE_TABLES))
    ):
        raise ValueError(
            "Duplicate table names "
            "found in BRONZE_TABLES."
        )

    print(
        f"Table config OK: "
        f"{actual_count} Bronze tables"
    )


# =========================================================
# 4. TEST CONNECTION
# =========================================================

def test_connection():
    """
    Run a minimal query before loading data so connection or configuration
    problems are found before the more expensive pipeline work begins.
    """
    with engine.connect() as conn:
        conn.execute(
            text("SELECT 1")
        )

    print(
        "SQL Server connection successful"
    )


# =========================================================
# 5. READ ONE BRONZE TABLE
# =========================================================

def read_bronze_table(
    table_name,
):
    """
    Read one Bronze table into a Pandas DataFrame and centralize the database
    access so downstream Data Quality functions do not need SQL logic.
    """
    # Escape closing brackets before inserting a configured name as a SQL
    # identifier.
    safe_table_name = (
        table_name.replace(
            "]",
            "]]",
        )
    )

    query = text(
        f"SELECT * "
        f"FROM [bronze]."
        f"[{safe_table_name}]"
    )

    with engine.connect() as conn:
        df = pd.read_sql_query(
            query,
            conn,
        )

    return df


# =========================================================
# 6. READ ALL BRONZE TABLES
# =========================================================

def read_all_bronze_tables():
    """
    Load every configured Bronze table once and retain the DataFrames in a
    dictionary so later stages reuse memory instead of querying SQL Server.

    Output:
        {
            "customers_raw": DataFrame,
            "products_raw": DataFrame,
            ...
        }
    """
    tables = {}

    for index, table_name in (
        enumerate(
            BRONZE_TABLES,
            start=1,
        )
    ):
        print(
            f"[{index}/"
            f"{EXPECTED_TABLE_COUNT}] "
            f"Loading bronze."
            f"{table_name}"
        )

        tables[
            table_name
        ] = read_bronze_table(
            table_name
        )

    return tables


# =========================================================
# 7. RUN DATA QUALITY SCAN
# =========================================================

def run_data_quality_scan(
    all_tables,
):
    """
    Run the independent Data Quality checks for every Bronze table and group
    their results for reporting. This phase only inspects data; it never cleans
    or overwrites Bronze.
    """
    results = {
        "table_profile": [],
        "column_profile": [],
        "missing": [],
        "primary_key": [],
        "exact_duplicates": [],
        "text_variants": [],
        "whitespace": [],
        "column_structure": [],
        "datatype": [],
        "datatype_issues": [],
    }

    for index, (
        table_name,
        df,
    ) in enumerate(
        all_tables.items(),
        start=1,
    ):
        print(
            f"[{index}/"
            f"{EXPECTED_TABLE_COUNT}] "
            f"Scanning bronze."
            f"{table_name}"
        )

        # Basic shape and missing-value diagnostics.
        results[
            "table_profile"
        ].append(
            profile_table(
                table_name,
                df,
            )
        )

        # Key and duplicate diagnostics.
        results[
            "column_profile"
        ].extend(
            profile_columns(
                table_name,
                df,
            )
        )

        # Text-quality and structural diagnostics.
        results[
            "missing"
        ].extend(
            check_missing_values(
                table_name,
                df,
            )
        )

        results[
            "primary_key"
        ].append(
            check_primary_key(
                table_name,
                df,
            )
        )

        results[
            "exact_duplicates"
        ].extend(
            check_exact_duplicates(
                table_name,
                df,
            )
        )

        results[
            "text_variants"
        ].extend(
            check_text_variants(
                table_name,
                df,
            )
        )

        results[
            "whitespace"
        ].extend(
            check_whitespace_issues(
                table_name,
                df,
            )
        )

        results[
            "column_structure"
        ].extend(
            check_column_structure(
                table_name,
                df,
            )
        )

        # Infer candidate Silver datatypes and capture values that violate them.
        datatype_rows = (
            suggest_datatypes(
                table_name,
                df,
            )
        )

        results[
            "datatype"
        ].extend(
            datatype_rows
        )

        results[
            "datatype_issues"
        ].extend(
            check_datatype_issues(
                table_name,
                df,
                datatype_rows,
            )
        )

    return results


# =========================================================
# 8. SAVE CSV HELPER
# =========================================================

def save_csv_report(
    data,
    filename,
    columns,
):
    """
    Write a report with shared CSV settings and route it to the folder for its
    report type so generated outputs stay organized.
    """

    profile_files = {
        "bronze_table_profile.csv",
        "bronze_column_profile.csv",
    }

    proposal_files = {
        "silver_datatype_suggestions.csv",
    }

    # Keep profile, Data Quality, and proposal files in separate directories.
    if filename in profile_files:
        target_dir = PROFILE_DIR

    elif filename in proposal_files:
        target_dir = PROPOSAL_DIR

    else:
        target_dir = DQ_DIR

    output_path = (
        target_dir
        / filename
    )

    report_df = pd.DataFrame(
        data,
        columns=columns,
    )

    report_df.to_csv(
        output_path,
        index=False,
        encoding="utf-8-sig",
    )

    return output_path


# =========================================================
# 9. SAVE DATA QUALITY REPORTS
# =========================================================

def save_data_quality_reports(
    results,
):
    """
    Convert each Data Quality result group into its corresponding CSV report.
    Keeping output separate from scanning lets the analysis rules stay focused
    on inspection rather than file management.
    """
    report_paths = []

    report_paths.append(
        save_csv_report(
            results["table_profile"],
            "bronze_table_profile.csv",
            [
                "table_name",
                "row_count",
                "column_count",
                "real_null_cells",
                "exact_duplicate_rows",
            ],
        )
    )

    report_paths.append(
        save_csv_report(
            results["column_profile"],
            "bronze_column_profile.csv",
            [
                "table_name",
                "column_name",
                "pandas_dtype",
                "row_count",
                "real_null_count",
                "marker_null_count",
                "missing_count",
                "missing_pct",
                "unique_count",
                "effective_unique_count",
            ],
        )
    )

    report_paths.append(
        save_csv_report(
            results["missing"],
            "dq_missing_values.csv",
            [
                "table_name",
                "column_name",
                "row_count",
                "real_null_count",
                "marker_null_count",
                "missing_count",
                "missing_pct",
                "marker_examples",
            ],
        )
    )

    report_paths.append(
        save_csv_report(
            results["primary_key"],
            "dq_primary_key.csv",
            [
                "table_name",
                "pk_column",
                "row_count",
                "missing_pk_count",
                "duplicate_pk_groups",
                "duplicate_pk_rows",
                "normalized_duplicate_pk_groups",
                "status",
            ],
        )
    )

    report_paths.append(
        save_csv_report(
            results["exact_duplicates"],
            "dq_exact_duplicates.csv",
            [
                "table_name",
                "duplicate_group_count",
                "affected_rows",
            ],
        )
    )

    report_paths.append(
        save_csv_report(
            results["text_variants"],
            "dq_text_variants.csv",
            [
                "table_name",
                "column_name",
                "issue_type",
                "comparison_key",
                "variant_count",
                "affected_rows",
                "variants",
            ],
        )
    )

    report_paths.append(
        save_csv_report(
            results["whitespace"],
            "dq_whitespace_issues.csv",
            [
                "table_name",
                "column_name",
                "leading_trailing_space_count",
                "repeated_space_count",
                "tab_newline_count",
                "total_whitespace_issue_count",
            ],
        )
    )

    report_paths.append(
        save_csv_report(
            results[
                "column_structure"
            ],
            "dq_column_structure.csv",
            [
                "table_name",
                "column_name",
                "issue_type",
                "non_missing_count",
                "unique_non_missing_count",
            ],
        )
    )

    report_paths.append(
        save_csv_report(
            results["datatype"],
            "silver_datatype_suggestions.csv",
            [
                "table_name",
                "column_name",
                "suggested_silver_type",
                "numeric_ratio",
                "date_ratio",
            ],
        )
    )

    report_paths.append(
        save_csv_report(
            results[
                "datatype_issues"
            ],
            "dq_datatype_issues.csv",
            [
                "table_name",
                "column_name",
                "suggested_silver_type",
                "invalid_value_count",
                "invalid_examples",
            ],
        )
    )

    return report_paths


# =========================================================
# 10. BUILD SILVER SCHEMA PROPOSAL
# =========================================================

def run_schema_proposal(
    all_tables,
):
    """
    Bridge Data Quality evidence to the proposed Silver schema outputs. The
    generated CSV and editable JSON are review material, not the final approved
    Silver schema.
    """
    (
        master_json,
        column_report,
        key_report,
    ) = build_schema_proposal(
        all_tables
    )

    paths = export_schema_proposal(
        master_json=master_json,
        column_report=column_report,
        key_report=key_report,
        report_dir=REPORT_DIR,
        review_dir=REVIEW_DIR,
    )

    return paths


# =========================================================
# 11. PRINT OUTPUT
# =========================================================

def print_output_summary(
    dq_report_paths,
    proposal_paths,
):
    """
    Show completion status and point the operator to the review artifacts and
    the manual checkpoint that must happen before any cleaning work.
    """
    print()
    print("=" * 60)
    print(
        "PHASE 1 COMPLETED - "
        "NO DATA CLEANING APPLIED"
    )
    print("=" * 60)

    print()
    print(
        "DATA QUALITY REPORTS:"
    )

    for path in dq_report_paths:
        print(
            f"- {path.name}"
        )

    print()
    print(
        "SILVER PROPOSAL OUTPUT:"
    )

    print(
        "- "
        + proposal_paths[
            "column_report"
        ].name
    )

    print(
        "- "
        + proposal_paths[
            "key_report"
        ].name
    )

    print(
        "- review/"
        + proposal_paths[
            "review_json"
        ].name
    )

    print()
    print(
        "Next step: review/edit "
        "silver_schema_review.json. "
        "Do NOT clean yet."
    )


# =========================================================
# 12. MAIN
# =========================================================

def main():
    """
    Execute Phase 1 in order: validate configuration, test the database, load
    Bronze, run Data Quality, save reports, and build the schema proposal.
    """
    print("=" * 60)
    print(
        "SILVER PHASE 1 - "
        "PROFILE + PROPOSAL"
    )
    print("=" * 60)

    # Step 1
    validate_table_config()

    # Step 2
    test_connection()

    # Step 3
    all_tables = (
        read_all_bronze_tables()
    )

    # Step 4
    dq_results = (
        run_data_quality_scan(
            all_tables
        )
    )

    # Step 5
    dq_report_paths = (
        save_data_quality_reports(
            dq_results
        )
    )

    # Step 6
    proposal_paths = (
        run_schema_proposal(
            all_tables
        )
    )

    # Step 7
    print_output_summary(
        dq_report_paths,
        proposal_paths,
    )


if __name__ == "__main__":
    main()
