"""Validate standardized Silver DataFrames before SQL Server write."""

from __future__ import annotations

from decimal import Decimal
from typing import Dict, List, Tuple

import pandas as pd

from standardization.standardize_silver import resolve_target_column
from standardization.decision_policy import table_action_is_fixed, table_issue_is_kept


def _issue(
    table_name: str,
    column_name: str,
    issue_type: str,
    issue_count: int,
    severity: str,
    example: str = "",
) -> Dict:
    """Create one normalized validation issue row for reporting."""

    return {
        "table_name": table_name,
        "column_name": column_name,
        "issue_type": issue_type,
        "issue_count": int(issue_count),
        "example": example,
        "severity": severity,
    }


def validate_required_columns(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
) -> List[Dict]:
    """Ensure every analyst-approved Silver column exists after transformation."""

    expected = {config["name"] for config in table_schema.get("columns", {}).values()}
    missing = sorted(expected - set(df.columns))
    if not missing:
        return []
    return [
        _issue(
            table_name,
            "",
            "MISSING_REQUIRED_COLUMN",
            len(missing),
            "FAIL",
            ", ".join(missing),
        )
    ]


def validate_nullability(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
) -> List[Dict]:
    """Fail when a reviewed non-nullable column still contains NULL values."""

    issues: List[Dict] = []
    for _, config in table_schema.get("columns", {}).items():
        column = config["name"]
        if column not in df.columns or config.get("nullable", True):
            continue
        count = int(df[column].isna().sum())
        if count:
            issues.append(_issue(table_name, column, "NON_NULLABLE_HAS_NULL", count, "FAIL"))
    return issues


def validate_primary_key(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
) -> List[Dict]:
    """Validate reviewed PK completeness and uniqueness without changing data."""

    pk = [
        resolve_target_column(table_schema, column)
        for column in table_schema.get("table", {}).get("primary_key", [])
    ]
    if not pk:
        if bool(table_schema.get("table", {}).get("allow_no_primary_key", False)):
            return []
        return [
            _issue(
                table_name,
                "",
                "NO_REVIEWED_PRIMARY_KEY",
                1,
                "FAIL",
                "No PK selected and allow_no_primary_key is false.",
            )
        ]

    issues: List[Dict] = []
    null_rows = int(df[pk].isna().any(axis=1).sum())
    if null_rows:
        issues.append(_issue(table_name, " + ".join(pk), "PK_NULL", null_rows, "FAIL"))

    duplicate_rows = int(df.duplicated(subset=pk, keep=False).sum())
    if duplicate_rows:
        sample = df.loc[df.duplicated(subset=pk, keep=False), pk].head(5)
        issues.append(
            _issue(
                table_name,
                " + ".join(pk),
                "PK_DUPLICATE",
                duplicate_rows,
                "FAIL",
                str(sample.astype("string").to_dict(orient="records")),
            )
        )
    return issues


def validate_foreign_keys(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
    all_silver_tables: Dict[str, pd.DataFrame],
    review_schema: Dict,
) -> List[Dict]:
    """Validate approved FK relationships against standardized parent tables."""

    issues: List[Dict] = []
    for fk in table_schema.get("table", {}).get("foreign_keys", []):
        child_column = resolve_target_column(table_schema, fk["column"])
        parent_table, parent_identifier = fk["references"].split(".", 1)
        if parent_table not in all_silver_tables or parent_table not in review_schema:
            issues.append(
                _issue(
                    table_name,
                    child_column,
                    "FK_PARENT_MISSING",
                    1,
                    "FAIL",
                    fk["references"],
                )
            )
            continue

        parent_schema = review_schema[parent_table]
        parent_column = resolve_target_column(parent_schema, parent_identifier)
        parent_df = all_silver_tables[parent_table]
        if child_column not in df.columns or parent_column not in parent_df.columns:
            issues.append(
                _issue(
                    table_name,
                    child_column,
                    "FK_COLUMN_MISSING",
                    1,
                    "FAIL",
                    fk["references"],
                )
            )
            continue

        child_values = df[child_column].dropna().astype("string").str.strip()
        parent_values = set(parent_df[parent_column].dropna().astype("string").str.strip())
        orphan_mask = ~child_values.isin(parent_values)
        orphan_count = int(orphan_mask.sum())
        if orphan_count:
            examples = child_values[orphan_mask].drop_duplicates().head(5).tolist()
            issues.append(
                _issue(
                    table_name,
                    child_column,
                    "FK_ORPHAN",
                    orphan_count,
                    "FAIL",
                    " | ".join(map(str, examples)),
                )
            )
    return issues


def validate_exact_duplicates(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
) -> List[Dict]:
    """Validate exact duplicates according to the analyst decision.

    - FIX + REMOVE_EXACT_DUPLICATES: duplicates must be gone.
    - KEEP: remaining exact duplicates are an accepted business exception and
      are reported as WARNING rather than FAIL.
    - No decision: remaining duplicates fail closed.
    """

    count = int(df.duplicated(keep=False).sum())
    if count == 0:
        return []

    if table_issue_is_kept(table_schema, "EXACT_DUPLICATE"):
        return [
            _issue(
                table_name,
                "",
                "EXACT_DUPLICATE_ACCEPTED",
                count,
                "WARNING",
                "Analyst decision=KEEP; duplicate rows are intentionally preserved.",
            )
        ]

    if table_action_is_fixed(
        table_schema, "EXACT_DUPLICATE", "REMOVE_EXACT_DUPLICATES"
    ):
        return [
            _issue(
                table_name,
                "",
                "EXACT_DUPLICATE",
                count,
                "FAIL",
                "Decision=FIX but exact duplicates remain after standardization.",
            )
        ]

    return [
        _issue(
            table_name,
            "",
            "EXACT_DUPLICATE_UNDECIDED",
            count,
            "FAIL",
            "Exact duplicates remain without an executable FIX or accepted KEEP decision.",
        )
    ]


def validate_datatypes(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
) -> List[Dict]:
    """Check whether standardized columns match the reviewed logical datatype."""

    issues: List[Dict] = []
    for _, config in table_schema.get("columns", {}).items():
        column = config["name"]
        datatype = str(config["datatype"]).lower()
        if column not in df.columns:
            continue
        series = df[column]
        valid = True

        if datatype == "string":
            valid = pd.api.types.is_string_dtype(series.dtype)
        elif datatype == "integer":
            valid = pd.api.types.is_integer_dtype(series.dtype)
        elif datatype == "datetime":
            valid = pd.api.types.is_datetime64_any_dtype(series.dtype)
        elif datatype == "boolean":
            valid = pd.api.types.is_bool_dtype(series.dtype)
        elif datatype == "date":
            non_null = series.dropna()
            valid = all(hasattr(value, "year") and not isinstance(value, pd.Timestamp) for value in non_null.head(1000))
        elif datatype == "decimal":
            non_null = series.dropna()
            valid = all(isinstance(value, Decimal) for value in non_null.head(1000))

        if not valid:
            issues.append(
                _issue(
                    table_name,
                    column,
                    "DATATYPE_VALIDATION",
                    1,
                    "FAIL",
                    f"Expected {datatype}; pandas dtype={series.dtype}",
                )
            )
    return issues


def validate_currency(table_name: str, df: pd.DataFrame) -> List[Dict]:
    """Validate canonical currency-code columns as three uppercase letters."""

    issues: List[Dict] = []
    currency_columns = [
        column
        for column in df.columns
        if column == "currency"
        or column == "currency_code"
        or (column.endswith("_currency") and not column.endswith("_per_currency"))
        or column.endswith("_currency_code")
    ]
    for column in currency_columns:
        non_null = df[column].dropna().astype("string")
        invalid_mask = ~non_null.str.fullmatch(r"[A-Z]{3}")
        count = int(invalid_mask.sum())
        if count:
            examples = non_null[invalid_mask].drop_duplicates().head(5).tolist()
            issues.append(
                _issue(
                    table_name,
                    column,
                    "INVALID_CURRENCY_CODE",
                    count,
                    "FAIL",
                    " | ".join(map(str, examples)),
                )
            )
    return issues


def validate_all_tables(
    silver_tables: Dict[str, pd.DataFrame],
    review_schema: Dict,
    standardization_issues: pd.DataFrame | None = None,
) -> Tuple[pd.DataFrame, Dict[str, str]]:
    """Run all Silver validations and return issue details plus per-table status."""

    issue_rows: List[Dict] = []
    if standardization_issues is not None and not standardization_issues.empty:
        issue_rows.extend(standardization_issues.to_dict(orient="records"))

    for table_name, df in silver_tables.items():
        table_schema = review_schema[table_name]
        issue_rows.extend(validate_required_columns(table_name, df, table_schema))
        issue_rows.extend(validate_nullability(table_name, df, table_schema))
        issue_rows.extend(validate_primary_key(table_name, df, table_schema))
        issue_rows.extend(
            validate_foreign_keys(
                table_name,
                df,
                table_schema,
                silver_tables,
                review_schema,
            )
        )
        issue_rows.extend(validate_exact_duplicates(table_name, df, table_schema))
        issue_rows.extend(validate_datatypes(table_name, df, table_schema))
        issue_rows.extend(validate_currency(table_name, df))

    issues_df = pd.DataFrame(
        issue_rows,
        columns=[
            "table_name",
            "column_name",
            "issue_type",
            "issue_count",
            "example",
            "severity",
        ],
    )

    status: Dict[str, str] = {}
    for table_name in silver_tables:
        if issues_df.empty:
            status[table_name] = "PASS"
            continue
        table_issues = issues_df[issues_df["table_name"].eq(table_name)]
        if table_issues["severity"].eq("FAIL").any():
            status[table_name] = "FAIL"
        elif table_issues["severity"].eq("WARNING").any():
            status[table_name] = "WARNING"
        else:
            status[table_name] = "PASS"

    return issues_df, status
