"""Entry point 1: inspect Bronze data and prepare analyst review files.

This stage is deliberately read-only. It profiles Bronze, reports potential data
quality issues, prepares an editable schema review CSV, and prepares an editable
issue-decision CSV. It does not clean data and does not create the final build
contract. Run ``review_to_json.py`` after the analyst finishes both reviews.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import quote_plus

import pandas as pd
from sqlalchemy import create_engine, text

from config.table_config import (
    BRONZE_SCHEMA,
    BRONZE_TABLES,
    DATABASE,
    EXPECTED_TABLE_COUNT,
    ODBC_DRIVER,
    SERVER,
)
from profiling.data_quality import (
    check_column_structure,
    check_datatype_issues,
    check_exact_duplicates,
    check_missing_values,
    check_text_variants,
    check_whitespace_issues,
    profile_table,
)
from profiling.schema_proposal import (
    build_review_dataframe,
    build_schema_proposal,
    export_review_files,
)

BASE_DIR = Path(__file__).resolve().parent
REPORT_DIR = BASE_DIR / "reports"
REVIEW_DIR = BASE_DIR / "review"


# =========================================================
# DATABASE
# =========================================================


def create_sql_engine():
    """Create the SQLAlchemy engine used to read SEPRO Bronze tables."""

    connection_string = quote_plus(
        f"DRIVER={{{ODBC_DRIVER}}};"
        f"SERVER={SERVER};"
        f"DATABASE={DATABASE};"
        "Trusted_Connection=yes;"
        "TrustServerCertificate=yes;"
    )
    return create_engine(f"mssql+pyodbc:///?odbc_connect={connection_string}")


def validate_table_config() -> None:
    """Fail early when the configured Bronze inventory is incomplete or duplicated."""

    actual_count = len(BRONZE_TABLES)
    if actual_count != EXPECTED_TABLE_COUNT:
        raise ValueError(
            f"Expected {EXPECTED_TABLE_COUNT} Bronze tables but found {actual_count}."
        )
    if len(BRONZE_TABLES) != len(set(BRONZE_TABLES)):
        raise ValueError("Duplicate table names found in BRONZE_TABLES.")


def test_connection(engine) -> None:
    """Run a minimal query so connection errors appear before expensive profiling."""

    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))


def _safe_identifier(identifier: str) -> str:
    """Escape a configured SQL Server identifier for bracket quoting."""

    return str(identifier).replace("]", "]]" )


def read_bronze_table(engine, table_name: str) -> pd.DataFrame:
    """Read one configured Bronze table into a pandas DataFrame."""

    safe_schema = _safe_identifier(BRONZE_SCHEMA)
    safe_table = _safe_identifier(table_name)
    query = text(f"SELECT * FROM [{safe_schema}].[{safe_table}]")
    with engine.connect() as connection:
        return pd.read_sql_query(query, connection)


def read_all_bronze_tables(engine) -> dict[str, pd.DataFrame]:
    """Load all configured Bronze tables exactly once for profiling and proposal work."""

    tables: dict[str, pd.DataFrame] = {}
    for index, table_name in enumerate(BRONZE_TABLES, start=1):
        print(f"[{index}/{EXPECTED_TABLE_COUNT}] Loading {BRONZE_SCHEMA}.{table_name}")
        tables[table_name] = read_bronze_table(engine, table_name)
    return tables


# =========================================================
# PROFILE + DQ REPORT
# =========================================================


def run_data_quality_scan(
    all_tables: dict[str, pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Profile Bronze and report potential issues without deciding how to fix them."""

    table_profiles = []
    issues = []

    for index, (table_name, df) in enumerate(all_tables.items(), start=1):
        print(f"[{index}/{EXPECTED_TABLE_COUNT}] Profiling {table_name}")
        table_profiles.append(profile_table(table_name, df))
        issues.extend(check_missing_values(table_name, df))
        issues.extend(check_exact_duplicates(table_name, df))
        issues.extend(check_text_variants(table_name, df))
        issues.extend(check_whitespace_issues(table_name, df))
        issues.extend(check_column_structure(table_name, df))
        issues.extend(check_datatype_issues(table_name, df))

    profile_df = pd.DataFrame(
        table_profiles,
        columns=[
            "table_name",
            "row_count",
            "column_count",
            "real_null_cells",
            "exact_duplicate_rows",
        ],
    ).rename(columns={"exact_duplicate_rows": "duplicate_row_count"})

    issues_df = pd.DataFrame(
        issues,
        columns=[
            "table_name",
            "column_name",
            "issue_type",
            "issue_count",
            "example",
            "detail",
        ],
    )
    return profile_df, issues_df


def save_reports(profile_df: pd.DataFrame, issues_df: pd.DataFrame) -> dict[str, Path]:
    """Save machine-generated Bronze profile and Data Quality findings."""

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    profile_path = REPORT_DIR / "bronze_profile.csv"
    dq_path = REPORT_DIR / "data_quality_report.csv"
    profile_df.to_csv(profile_path, index=False, encoding="utf-8-sig")
    issues_df.to_csv(dq_path, index=False, encoding="utf-8-sig")
    return {"profile": profile_path, "dq": dq_path}


# =========================================================
# DATA QUALITY DECISION REVIEW
# =========================================================


SUGGESTED_ACTIONS = {
    "MISSING_VALUE": "STANDARDIZE_NULL_MARKERS",
    "EXACT_DUPLICATE": "REMOVE_EXACT_DUPLICATES",
    "TEXT_VARIANT": "APPLY_VALUE_MAPPING",
    "WHITESPACE": "STANDARDIZE_TEXT",
    "ALL_MISSING_COLUMN": "REVIEW_SCHEMA",
    "CONSTANT_COLUMN": "REVIEW_SCHEMA",
    "DATATYPE_PARSE": "REVIEW_SCHEMA_DATATYPE",
}


def _issue_id(row: pd.Series) -> str:
    """Create a stable short identifier for one detected issue."""

    issue_type = str(row.get("issue_type", ""))
    # Most checks produce one row per table/column/issue type. TEXT_VARIANT can
    # produce multiple normalized groups in the same column, so only that check
    # needs its detail field in the stable identity. Dynamic counts are excluded.
    identity_detail = str(row.get("detail", "")) if issue_type == "TEXT_VARIANT" else ""
    raw_key = "\x1f".join(
        [
            str(row.get("table_name", "")),
            str(row.get("column_name", "")),
            issue_type,
            identity_detail,
        ]
    )
    return hashlib.sha1(raw_key.encode("utf-8")).hexdigest()[:12]


def build_quality_decision_review(issues_df: pd.DataFrame) -> pd.DataFrame:
    """Add analyst decision fields to machine-detected Data Quality issues."""

    columns = [
        "issue_id",
        "table_name",
        "column_name",
        "issue_type",
        "issue_count",
        "example",
        "detail",
        "suggested_action",
        "decision",
        "action",
        "reason",
    ]

    if issues_df.empty:
        return pd.DataFrame(columns=columns)

    review = issues_df.copy()
    review["issue_id"] = review.apply(_issue_id, axis=1)
    review["suggested_action"] = review["issue_type"].map(SUGGESTED_ACTIONS).fillna("REVIEW_ONLY")
    review["decision"] = ""
    review["action"] = ""
    review["reason"] = ""
    return review[columns]


def merge_quality_decisions(
    current_review: pd.DataFrame,
    existing_review: pd.DataFrame | None,
) -> pd.DataFrame:
    """Refresh issue counts/examples while preserving prior analyst decisions."""

    if existing_review is None or existing_review.empty or "issue_id" not in existing_review.columns:
        return current_review

    manual_columns = ["issue_id", "decision", "action", "reason"]
    available = [column for column in manual_columns if column in existing_review.columns]
    previous = existing_review[available].copy()
    merged = current_review.merge(previous, on="issue_id", how="left", suffixes=("", "__old"))

    for column in ("decision", "action", "reason"):
        old_column = f"{column}__old"
        if old_column not in merged.columns:
            continue
        merged[column] = merged[old_column].fillna(merged[column])
        merged = merged.drop(columns=[old_column])

    return merged[current_review.columns]


def save_quality_decisions(issues_df: pd.DataFrame) -> Path:
    """Write/refresh the analyst-editable issue-decision CSV."""

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    path = REVIEW_DIR / "data_quality_decisions.csv"
    current = build_quality_decision_review(issues_df)

    existing = None
    if path.exists():
        try:
            existing = pd.read_csv(path, keep_default_na=False)
        except Exception:
            existing = None

    merged = merge_quality_decisions(current, existing)
    merged.to_csv(path, index=False, encoding="utf-8-sig")
    return path


# =========================================================
# SCHEMA REVIEW OUTPUT
# =========================================================


def generate_review_files(
    all_tables: dict[str, pd.DataFrame],
    profile_df: pd.DataFrame,
) -> dict[str, Path]:
    """Generate machine schema proposals and refresh the editable schema Review CSV."""

    master_json, _, _ = build_schema_proposal(all_tables)
    review_profile = profile_df.rename(
        columns={"duplicate_row_count": "exact_duplicate_rows"}
    )
    proposal_df = build_review_dataframe(master_json, review_profile)

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    return export_review_files(master_json, proposal_df, REVIEW_DIR)


def print_summary(
    report_paths: dict[str, Path],
    review_paths: dict[str, Path],
    decision_path: Path,
) -> None:
    """Show the analyst which files to review before compiling the final contract."""

    print("\n" + "=" * 70)
    print("REVIEW STAGE COMPLETED - BRONZE DATA WAS NOT MODIFIED")
    print("=" * 70)
    print(f"Machine report: {report_paths['profile']}")
    print(f"Machine report: {report_paths['dq']}")
    print(f"Machine schema proposal: {review_paths['proposal_csv']}")
    print(f"Editable schema review: {review_paths['review_csv']}")
    print(f"Editable DQ decisions: {decision_path}")
    print("\nAnalyst workflow:")
    print("1. Review/edit review/silver_schema_review.csv")
    print("2. Review/edit review/data_quality_decisions.csv")
    print("3. Run: python review_to_json.py")
    print("4. If the contract compiles successfully, run: python build_silver.py")


# =========================================================
# MAIN
# =========================================================


def main() -> None:
    """Run the complete read-only Review stage from Bronze to analyst review files."""

    print("=" * 70)
    print("SEPRO SILVER - REVIEW STAGE")
    print("=" * 70)

    validate_table_config()
    engine = create_sql_engine()
    test_connection(engine)
    all_tables = read_all_bronze_tables(engine)
    profile_df, issues_df = run_data_quality_scan(all_tables)
    report_paths = save_reports(profile_df, issues_df)
    review_paths = generate_review_files(all_tables, profile_df)
    decision_path = save_quality_decisions(issues_df)
    print_summary(report_paths, review_paths, decision_path)


if __name__ == "__main__":
    main()
