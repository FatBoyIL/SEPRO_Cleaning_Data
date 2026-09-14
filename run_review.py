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
from profiling.data_quality import (
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

from profiling.schema_proposal import (
    build_schema_proposal,
    build_review_dataframe,
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

# =========================================================
# 1. PROJECT PATH
# =========================================================
#
# Review Phase chỉ cần 2 nơi output:
#
# reports/
#   - các report máy tạo tự động
#
# review/
#   - các file Data Analyst cần review/chỉnh sửa
# =========================================================

BASE_DIR = Path(
    __file__
).resolve().parent

REPORT_DIR = (
    BASE_DIR
    / "reports"
)

REVIEW_DIR = (
    BASE_DIR
    / "review"
)

# Tạo folder nếu chưa tồn tại.
for folder in (
    REPORT_DIR,
    REVIEW_DIR,
):
    folder.mkdir(
        parents=True,
        exist_ok=True,
    )

for folder in (
    REPORT_DIR,
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

def build_bronze_profile_report(
    results,
) -> pd.DataFrame:
    """
    Build a compact table-level overview of the Bronze layer.

    The function reuses table profiling results that were already
    calculated by run_data_quality_scan(), so Bronze data is not
    scanned a second time.

    Output contains one row per Bronze table and does not modify data.
    """

    profile_df = pd.DataFrame(
        results["table_profile"]
    ).copy()

    # Return an empty DataFrame with the expected schema if no
    # profiling results are available.
    if profile_df.empty:
        return pd.DataFrame(
            columns=[
                "table_name",
                "row_count",
                "column_count",
                "real_null_cells",
                "duplicate_row_count",
            ]
        )

    # Rename the existing technical metric so the final report
    # uses the simpler wording agreed for the project.
    profile_df = profile_df.rename(
        columns={
            "exact_duplicate_rows":
                "duplicate_row_count"
        }
    )

    return profile_df[
        [
            "table_name",
            "row_count",
            "column_count",
            "real_null_cells",
            "duplicate_row_count",
        ]
    ]
def safe_int(value, default=0) -> int:
    """
    Safely convert report metadata to integer.

    Empty strings, None, pandas missing values, or invalid numeric
    values are converted to the supplied default instead of causing
    the Review pipeline to fail.
    """

    if value is None:
        return default

    if isinstance(value, str) and value.strip() == "":
        return default

    try:
        if pd.isna(value):
            return default
    except (TypeError, ValueError):
        pass

    try:
        return int(value)
    except (TypeError, ValueError):
        return default
    
def build_data_quality_report(
    results,
) -> pd.DataFrame:
    """
    Combine detailed Data Quality checks into one concise issue report.

    Only detected issues are included. Datatype suggestions themselves
    are excluded because they are schema proposals rather than errors.

    The function only restructures existing profiling results and does
    not modify Bronze data.
    """

    issue_rows = []

    # -----------------------------------------------------
    # Create a row-count lookup for table-level issues.
    # -----------------------------------------------------
    table_row_counts = {
        row["table_name"]: row["row_count"]
        for row in results["table_profile"]
    }

    # -----------------------------------------------------
    # 1. Missing values
    # -----------------------------------------------------
    for row in results["missing"]:

        issue_count = int(
            row.get(
                "missing_count",
                0,
            )
        )

        if issue_count == 0:
            continue

        issue_rows.append(
            {
                "table_name":
                    row["table_name"],

                "column_name":
                    row["column_name"],

                "issue_type":
                    "MISSING_VALUE",

                "issue_count":
                    issue_count,

                "example":
                    row.get(
                        "marker_examples",
                        "",
                    ),
            }
        )

    # -----------------------------------------------------
    # 2. Primary-key issues
    # -----------------------------------------------------
    for row in results["primary_key"]:

        table_name = row.get(
            "table_name",
            "",
        )

        pk_column = row.get(
            "pk_column",
            "",
        )

    missing_pk_count = safe_int(
    row.get(
        "missing_pk_count",
        0,
    )
)

    duplicate_pk_rows = safe_int(
    row.get(
        "duplicate_pk_rows",
        0,
    )
)

    if missing_pk_count > 0:
            issue_rows.append(
                {
                    "table_name":
                        table_name,

                    "column_name":
                        pk_column,

                    "issue_type":
                        "PK_MISSING",

                    "issue_count":
                        missing_pk_count,

                    "example":
                        "",
                }
            )

    if duplicate_pk_rows > 0:
            issue_rows.append(
                {
                    "table_name":
                        table_name,

                    "column_name":
                        pk_column,

                    "issue_type":
                        "PK_DUPLICATE",

                    "issue_count":
                        duplicate_pk_rows,

                    "example":
                        "",
                }
            )

    # -----------------------------------------------------
    # 3. Exact duplicate rows
    # -----------------------------------------------------
    for row in results["exact_duplicates"]:

        affected_rows = int(
            row.get(
                "affected_rows",
                0,
            )
        )

        if affected_rows == 0:
            continue

        duplicate_groups = row.get(
            "duplicate_group_count",
            0,
        )

        issue_rows.append(
            {
                "table_name":
                    row["table_name"],

                "column_name":
                    "",

                "issue_type":
                    "EXACT_DUPLICATE",

                "issue_count":
                    affected_rows,

                "example":
                    (
                        f"duplicate_groups="
                        f"{duplicate_groups}"
                    ),
            }
        )

    # -----------------------------------------------------
    # 4. Text variants
    # -----------------------------------------------------
    for row in results["text_variants"]:

        affected_rows = int(
            row.get(
                "affected_rows",
                0,
            )
        )

        if affected_rows == 0:
            continue

        issue_rows.append(
            {
                "table_name":
                    row["table_name"],

                "column_name":
                    row["column_name"],

                "issue_type":
                    row.get(
                        "issue_type",
                        "TEXT_VARIANT",
                    ),

                "issue_count":
                    affected_rows,

                "example":
                    row.get(
                        "variants",
                        "",
                    ),
            }
        )

    # -----------------------------------------------------
    # 5. Whitespace issues
    # -----------------------------------------------------
    for row in results["whitespace"]:

        issue_count = int(
            row.get(
                "total_whitespace_issue_count",
                0,
            )
        )

        if issue_count == 0:
            continue

        leading_trailing = row.get(
            "leading_trailing_space_count",
            0,
        )

        repeated_space = row.get(
            "repeated_space_count",
            0,
        )

        tab_newline = row.get(
            "tab_newline_count",
            0,
        )

        issue_rows.append(
            {
                "table_name":
                    row["table_name"],

                "column_name":
                    row["column_name"],

                "issue_type":
                    "WHITESPACE",

                "issue_count":
                    issue_count,

                "example":
                    (
                        f"trim={leading_trailing}; "
                        f"repeated_space={repeated_space}; "
                        f"tab_newline={tab_newline}"
                    ),
            }
        )

    # -----------------------------------------------------
    # 6. Column structure
    # -----------------------------------------------------
    for row in results["column_structure"]:

        issue_type = row.get(
            "issue_type",
            "",
        )

        table_name = row[
            "table_name"
        ]

        # ALL_MISSING affects every row in the table.
        if issue_type == "ALL_MISSING":

            issue_count = int(
                table_row_counts.get(
                    table_name,
                    0,
                )
            )

        # A constant column contains one repeated business value.
        elif issue_type == "CONSTANT_COLUMN":

            issue_count = int(
                row.get(
                    "non_missing_count",
                    0,
                )
            )

        else:
            issue_count = 0

        issue_rows.append(
            {
                "table_name":
                    table_name,

                "column_name":
                    row["column_name"],

                "issue_type":
                    issue_type,

                "issue_count":
                    issue_count,

                "example":
                    (
                        "unique_non_missing="
                        + str(
                            row.get(
                                "unique_non_missing_count",
                                "",
                            )
                        )
                    ),
            }
        )

    # -----------------------------------------------------
    # 7. Datatype issues
    # -----------------------------------------------------
    for row in results["datatype_issues"]:

        issue_count = int(
            row.get(
                "invalid_value_count",
                0,
            )
        )

        if issue_count == 0:
            continue

        issue_rows.append(
            {
                "table_name":
                    row["table_name"],

                "column_name":
                    row["column_name"],

                "issue_type":
                    "DATATYPE",

                "issue_count":
                    issue_count,

                "example":
                    row.get(
                        "invalid_examples",
                        "",
                    ),
            }
        )

    # -----------------------------------------------------
    # Build the final compact report.
    # -----------------------------------------------------
    report_df = pd.DataFrame(
        issue_rows,
        columns=[
            "table_name",
            "column_name",
            "issue_type",
            "issue_count",
            "example",
        ],
    )

    if not report_df.empty:

        report_df = (
            report_df
            .sort_values(
                by=[
                    "table_name",
                    "column_name",
                    "issue_type",
                ],
                na_position="last",
            )
            .reset_index(
                drop=True
            )
        )

    return report_df
def save_compact_reports(
    results,
):
    """
    Save the two concise Review Phase reports.

    Outputs:
    - reports/bronze_profile.csv
    - reports/data_quality_report.csv

    Existing detailed reports are intentionally left untouched during
    this migration step so the new outputs can be compared safely.
    """

    bronze_profile = (
        build_bronze_profile_report(
            results
        )
    )

    data_quality_report = (
        build_data_quality_report(
            results
        )
    )

    bronze_profile_path = (
        REPORT_DIR
        / "bronze_profile.csv"
    )

    data_quality_path = (
        REPORT_DIR
        / "data_quality_report.csv"
    )

    bronze_profile.to_csv(
        bronze_profile_path,
        index=False,
        encoding="utf-8-sig",
    )

    data_quality_report.to_csv(
        data_quality_path,
        index=False,
        encoding="utf-8-sig",
    )

    return [
        bronze_profile_path,
        data_quality_path,
    ]



# =========================================================
# 10. BUILD SILVER SCHEMA PROPOSAL
# =========================================================

def run_schema_proposal(
    all_tables,
):
    """
    Build the proposed Silver schema and export the two files
    required for Data Analyst review.

    The machine-generated proposal is converted into:
    - silver_schema_review.csv
    - silver_schema_review.json

    No Bronze data is cleaned or modified.
    """

    (
        master_json,
        _column_report,
        _key_report,
    ) = build_schema_proposal(
        all_tables
    )

    # Convert the nested JSON proposal into a simple
    # column-level Review CSV.
    review_report = (
        build_review_dataframe(
            master_json=master_json,
            all_tables=all_tables,
        )
    )

    paths = export_schema_proposal(
        master_json=master_json,
        review_report=review_report,
        review_dir=REVIEW_DIR,
    )

    return paths


# =========================================================
# 11. PRINT OUTPUT
# =========================================================

def print_output_summary(
    compact_report_paths,
    proposal_paths,
):
    """
    Print a concise summary of Review Phase outputs.

    The function points the Data Analyst to the generated
    reports and the schema JSON that must be reviewed before
    any Silver data is built.
    """

    print()

    print(
        "=" * 60
    )

    print(
        "REVIEW PHASE COMPLETED"
    )

    print(
        "NO SILVER DATA WAS CREATED"
    )

    print(
        "=" * 60
    )

    print()

    print(
        "REPORTS:"
    )

    for path in compact_report_paths:
        print(
            f"- reports/{path.name}"
        )

    print()

    print(
        "SCHEMA REVIEW:"
    )

    print(
        "- review/"
        + proposal_paths[
            "review_csv"
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
        "NEXT STEP:"
    )

    print(
        "Review and edit "
        "review/silver_schema_review.json"
    )

    print(
        "Do not build Silver until "
        "the JSON review is complete."
    )


# =========================================================
# 12. MAIN
# =========================================================

def main():
    print("DONE")

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


    # Step 5.1
    compact_report_paths = (
        save_compact_reports(
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
        compact_report_paths,
        proposal_paths,
    )


if __name__ == "__main__":
    main()
