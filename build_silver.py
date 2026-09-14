"""Entry point 3: build Silver from the compiled analyst review contract.

Workflow:
    run_review.py -> edit both CSV reviews -> review_to_json.py -> build_silver.py

The build reads only ``review/silver_review_contract.json``. Automatic data-quality
fixes execute only when the analyst selected FIX; KEEP preserves accepted issues;
BLOCK prevents the build from proceeding.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Numeric,
    Unicode,
    create_engine,
    text,
)

from config.table_config import (
    BRONZE_SCHEMA,
    BRONZE_TABLES,
    DATABASE,
    ODBC_DRIVER,
    SERVER,
    SILVER_SCHEMA,
    SQL_CHUNKSIZE,
)
from standardization.standardize_silver import (
    get_silver_table_name,
    load_review_schema,
    load_standardization_rules,
    standardize_all_tables,
    validate_review_schema_structure,
)
from validation.validate_silver import validate_all_tables
from standardization.decision_policy import blocking_decisions

BASE_DIR = Path(__file__).resolve().parent
REVIEW_PATH = BASE_DIR / "review" / "silver_review_contract.json"
RULES_PATH = BASE_DIR / "standardization" / "rules" / "standardization_rules.json"
REPORT_DIR = BASE_DIR / "reports"
REVIEW_DIR = BASE_DIR / "review"


# =========================================================
# DATABASE
# =========================================================


def create_sql_engine():
    """Create the SQLAlchemy engine used for Bronze reads and Silver writes."""

    connection_string = quote_plus(
        f"DRIVER={{{ODBC_DRIVER}}};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )
    return create_engine(
        f"mssql+pyodbc:///?odbc_connect={connection_string}",
        fast_executemany=True,
    )


def _safe_identifier(identifier: str) -> str:
    """Escape a configured SQL Server identifier for bracket quoting."""

    return str(identifier).replace("]", "]]" )


def test_connection(engine) -> None:
    """Verify SQL Server access before loading or transforming data."""

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def read_bronze_table(engine, table_name: str) -> pd.DataFrame:
    """Read one configured Bronze table into memory for Silver transformation."""

    safe_schema = _safe_identifier(BRONZE_SCHEMA)
    safe_table = _safe_identifier(table_name)
    query = text(f"SELECT * FROM [{safe_schema}].[{safe_table}]")
    with engine.connect() as connection:
        return pd.read_sql_query(query, connection)


def read_all_bronze_tables(engine) -> dict[str, pd.DataFrame]:
    """Load every configured Bronze table exactly once for the Silver build."""

    tables: dict[str, pd.DataFrame] = {}
    for index, table_name in enumerate(BRONZE_TABLES, start=1):
        print(f"[{index}/{len(BRONZE_TABLES)}] Loading {BRONZE_SCHEMA}.{table_name}")
        tables[table_name] = read_bronze_table(engine, table_name)
    return tables


# =========================================================
# ANALYST DECISION PREFLIGHT
# =========================================================


def validate_decision_execution_rules(
    review_schema: dict,
    rules: dict,
) -> list[str]:
    """Check that approved FIX decisions have the supporting rule configuration.

    Most FIX actions are self-contained. ``APPLY_VALUE_MAPPING`` additionally
    requires a mapping in standardization_rules.json; otherwise the build would
    claim to fix a text variant without an executable business mapping.
    """

    errors: list[str] = []
    table_rules_root = rules.get("tables", {})

    for table_name, table_schema in review_schema.items():
        silver_name = get_silver_table_name(table_name, table_schema)
        table_rules = table_rules_root.get(
            table_name, table_rules_root.get(silver_name, {})
        )
        mappings = table_rules.get("value_mappings", {})

        for decision in table_schema.get("quality_decisions", []):
            if str(decision.get("decision", "")).upper() != "FIX":
                continue
            if str(decision.get("action", "")).upper() != "APPLY_VALUE_MAPPING":
                continue

            identifier = str(decision.get("column_name", "")).strip()
            target_column = identifier
            columns = table_schema.get("columns", {})
            if identifier in columns:
                target_column = columns[identifier].get("name", identifier)

            if target_column not in mappings:
                errors.append(
                    f"{table_name}.{identifier}: FIX/APPLY_VALUE_MAPPING requires "
                    f"tables.{table_name}.value_mappings.{target_column} (or the Silver table equivalent) "
                    "in standardization_rules.json."
                )

    return errors


# =========================================================
# REPORTING
# =========================================================


def save_standardization_summary(
    summary_df: pd.DataFrame,
    status_by_table: dict[str, str],
) -> Path:
    """Save the compact build summary requested for the Silver project."""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    output = summary_df.copy()
    output["status"] = output["table_name"].map(status_by_table).fillna("PASS")
    output = output[
        [
            "table_name",
            "input_rows",
            "output_rows",
            "changed_rows",
            "removed_duplicates",
            "status",
        ]
    ]
    path = REPORT_DIR / "standardization_summary.csv"
    output.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def build_before_after_review(
    summary_df: pd.DataFrame,
    validation_issues: pd.DataFrame,
    status_by_table: dict[str, str],
) -> pd.DataFrame:
    """Build the final concise Bronze-vs-Silver review table for analyst sign-off."""

    invalid_after_lookup = {}
    if validation_issues is not None and not validation_issues.empty:
        fail_rows = validation_issues[validation_issues["severity"].eq("FAIL")]
        invalid_after_lookup = (
            fail_rows.groupby("table_name")["issue_count"].sum().astype(int).to_dict()
        )

    rows = []
    for _, row in summary_df.iterrows():
        table_name = row["table_name"]
        rows.append(
            {
                "table_name": table_name,
                "input_rows": int(row["input_rows"]),
                "output_rows": int(row["output_rows"]),
                "changed_rows": int(row["changed_rows"]),
                "duplicate_before": int(row["duplicate_before"]),
                "duplicate_after": int(row["duplicate_after"]),
                "null_before": int(row["null_before"]),
                "null_after": int(row["null_after"]),
                "invalid_before": int(row["standardization_issue_count"]),
                "invalid_after": int(invalid_after_lookup.get(table_name, 0)),
                "status": status_by_table.get(table_name, "PASS"),
            }
        )
    return pd.DataFrame(rows)


def save_before_after_review(review_df: pd.DataFrame) -> Path:
    """Write ``review/silver_before_after_review.csv`` after standardization/validation."""

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEW_DIR / "silver_before_after_review.csv"
    review_df.to_csv(path, index=False, encoding="utf-8-sig")
    return path


def print_validation_issues(validation_issues: pd.DataFrame, max_rows: int = 50) -> None:
    """Print a compact validation error sample when the Silver build cannot proceed."""

    if validation_issues is None or validation_issues.empty:
        return
    failures = validation_issues[validation_issues["severity"].eq("FAIL")]
    if failures.empty:
        return
    print("\nCritical validation issues:")
    print(
        failures[
            ["table_name", "column_name", "issue_type", "issue_count", "example"]
        ]
        .head(max_rows)
        .to_string(index=False)
    )
    if len(failures) > max_rows:
        print(f"... and {len(failures) - max_rows} more issue rows.")


# =========================================================
# SQL SILVER WRITE
# =========================================================


def ensure_silver_schema(engine) -> None:
    """Create the SQL Server ``silver`` schema if it does not already exist."""

    safe_schema = _safe_identifier(SILVER_SCHEMA)
    sql = text(
        "IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = :schema_name) "
        f"EXEC('CREATE SCHEMA [{safe_schema}]')"
    )
    with engine.begin() as connection:
        connection.execute(sql, {"schema_name": SILVER_SCHEMA})


def build_sql_dtype_map(table_schema: dict, df: pd.DataFrame) -> dict:
    """Map reviewed logical datatypes to explicit SQL Server-compatible SQLAlchemy types."""

    dtype_map = {}
    logical_to_sql = {
        "string": Unicode(4000),
        "integer": BigInteger(),
        "decimal": Numeric(38, 10),
        "date": Date(),
        "datetime": DateTime(),
        "boolean": Boolean(),
    }

    for _, config in table_schema.get("columns", {}).items():
        column = config["name"]
        datatype = str(config["datatype"]).lower()
        if column in df.columns and datatype in logical_to_sql:
            dtype_map[column] = logical_to_sql[datatype]

    # Currency conversion can add derived columns that are intentionally not part
    # of the original Bronze schema review.
    for column in df.columns:
        if column in dtype_map:
            continue
        if column == "fx_rate_to_vnd" or column.endswith("_vnd"):
            dtype_map[column] = Numeric(38, 10)

    return dtype_map


def write_silver_tables(
    engine,
    silver_tables: dict[str, pd.DataFrame],
    review_schema: dict,
) -> None:
    """Replace SQL ``silver.*`` tables only after in-memory validation succeeds."""

    ensure_silver_schema(engine)
    for index, (source_table, df) in enumerate(silver_tables.items(), start=1):
        table_schema = review_schema[source_table]
        target_name = get_silver_table_name(source_table, table_schema)
        print(f"[{index}/{len(silver_tables)}] Writing {SILVER_SCHEMA}.{target_name}")
        dtype_map = build_sql_dtype_map(table_schema, df)
        df.to_sql(
            name=target_name,
            con=engine,
            schema=SILVER_SCHEMA,
            if_exists="replace",
            index=False,
            dtype=dtype_map,
            chunksize=SQL_CHUNKSIZE,
        )


def verify_silver_write(
    engine,
    silver_tables: dict[str, pd.DataFrame],
    review_schema: dict,
) -> None:
    """Verify that every written Silver table exists with the expected row count."""

    safe_schema = _safe_identifier(SILVER_SCHEMA)
    with engine.connect() as connection:
        for source_table, df in silver_tables.items():
            target_name = get_silver_table_name(source_table, review_schema[source_table])
            safe_table = _safe_identifier(target_name)
            query = text(f"SELECT COUNT_BIG(1) AS row_count FROM [{safe_schema}].[{safe_table}]")
            actual = int(connection.execute(query).scalar_one())
            expected = int(len(df))
            if actual != expected:
                raise RuntimeError(
                    f"Post-write row-count mismatch for {SILVER_SCHEMA}.{target_name}: "
                    f"expected {expected}, found {actual}."
                )


# =========================================================
# MAIN
# =========================================================


def main() -> None:
    """Build, validate, write, and verify the complete Silver Layer on demand."""

    print("=" * 70)
    print("SEPRO SILVER - BUILD STAGE")
    print("=" * 70)
    print(f"Using reviewed contract: {REVIEW_PATH}")

    engine = create_sql_engine()
    test_connection(engine)
    bronze_tables = read_all_bronze_tables(engine)

    review_schema = load_review_schema(REVIEW_PATH)
    rules = load_standardization_rules(RULES_PATH)
    schema_errors, schema_warnings = validate_review_schema_structure(
        review_schema, bronze_tables
    )

    for warning in schema_warnings:
        print(f"WARNING: {warning}")
    if schema_errors:
        print("\nReview contract is not valid for building Silver:")
        for error in schema_errors:
            print(f"- {error}")
        raise SystemExit(1)

    blocked = blocking_decisions(review_schema)
    if blocked:
        print("\nSILVER BUILD BLOCKED by analyst decision(s):")
        for item in blocked:
            print(
                f"- {item['table_name']}.{item.get('column_name', '')} "
                f"{item.get('issue_type')}: {item.get('reason', '')}"
            )
        print("Resolve the BLOCK rows in data_quality_decisions.csv and rerun review_to_json.py.")
        raise SystemExit(1)

    decision_rule_errors = validate_decision_execution_rules(review_schema, rules)
    if decision_rule_errors:
        print("\nSome FIX decisions do not have executable rules:")
        for error in decision_rule_errors:
            print(f"- {error}")
        raise SystemExit(1)

    silver_tables, summary_df, standardization_issues = standardize_all_tables(
        bronze_tables=bronze_tables,
        review_schema=review_schema,
        rules=rules,
    )

    validation_issues, status_by_table = validate_all_tables(
        silver_tables=silver_tables,
        review_schema=review_schema,
        standardization_issues=standardization_issues,
    )

    summary_path = save_standardization_summary(summary_df, status_by_table)
    before_after_df = build_before_after_review(
        summary_df, validation_issues, status_by_table
    )
    review_path = save_before_after_review(before_after_df)

    has_failures = any(status == "FAIL" for status in status_by_table.values())
    if has_failures:
        print_validation_issues(validation_issues)
        print("\nSILVER BUILD STOPPED. No silver.* table was written because validation failed.")
        print(f"Summary: {summary_path}")
        print(f"Before/After Review: {review_path}")
        raise SystemExit(1)

    write_silver_tables(engine, silver_tables, review_schema)
    verify_silver_write(engine, silver_tables, review_schema)

    print("\n" + "=" * 70)
    print("SILVER LAYER COMPLETED")
    print("=" * 70)
    print(f"SQL output: {DATABASE}.{SILVER_SCHEMA}.*")
    print(f"Summary: {summary_path}")
    print(f"Before/After Review: {review_path}")


if __name__ == "__main__":
    main()
