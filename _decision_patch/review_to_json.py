"""Compile analyst-reviewed CSV files into the final Silver build contract.

Human decision files:
- review/silver_schema_review.csv
- review/data_quality_decisions.csv

Machine contract:
- review/silver_review_contract.json

The build stage reads only the final JSON contract. This keeps Excel/CSV as the
human review surface and JSON as the deterministic execution format.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).resolve().parent
REVIEW_DIR = BASE_DIR / "review"
SCHEMA_REVIEW_PATH = REVIEW_DIR / "silver_schema_review.csv"
DQ_DECISIONS_PATH = REVIEW_DIR / "data_quality_decisions.csv"
CONTRACT_PATH = REVIEW_DIR / "silver_review_contract.json"

ALLOWED_DATATYPES = {"string", "integer", "decimal", "date", "datetime", "boolean"}
ALLOWED_DECISIONS = {"FIX", "KEEP", "BLOCK"}

ALLOWED_FIX_ACTIONS = {
    "STANDARDIZE_NULL_MARKERS",
    "REMOVE_EXACT_DUPLICATES",
    "APPLY_VALUE_MAPPING",
    "STANDARDIZE_TEXT",
    "REVIEW_SCHEMA",
    "REVIEW_SCHEMA_DATATYPE",
    "REVIEW_ONLY",
}


# =========================================================
# BASIC PARSERS
# =========================================================


def _text(value) -> str:
    """Return a trimmed string while treating pandas missing values as empty."""

    if pd.isna(value):
        return ""
    return str(value).strip()


def _bool(value, field_name: str) -> bool:
    """Parse common CSV boolean representations or raise a clear review error."""

    if isinstance(value, bool):
        return value
    normalized = _text(value).lower()
    true_values = {"true", "1", "yes", "y"}
    false_values = {"false", "0", "no", "n", ""}
    if normalized in true_values:
        return True
    if normalized in false_values:
        return False
    raise ValueError(f"Invalid boolean for {field_name}: {value!r}")


def _pk_order(value, fallback: int) -> int:
    """Parse optional composite-PK order, falling back to CSV row order."""

    text = _text(value)
    if not text:
        return fallback
    try:
        number = int(float(text))
    except ValueError as error:
        raise ValueError(f"Invalid pk_order: {value!r}") from error
    if number < 1:
        raise ValueError(f"pk_order must be >= 1, found {number}")
    return number


# =========================================================
# SCHEMA CSV -> CONTRACT
# =========================================================


def compile_schema_review(schema_df: pd.DataFrame) -> tuple[dict, list[str]]:
    """Compile the analyst schema CSV into the table/column contract structure."""

    required = {
        "table_name",
        "silver_table_name",
        "allow_no_primary_key",
        "source_column",
        "column_name",
        "datatype",
        "nullable",
        "is_pk",
        "pk_order",
        "is_fk",
        "fk_reference",
    }
    missing = sorted(required - set(schema_df.columns))
    if missing:
        raise ValueError(
            "silver_schema_review.csv is missing columns: " + ", ".join(missing)
        )

    contract: dict = {}
    errors: list[str] = []

    for table_name, group in schema_df.groupby("table_name", sort=False, dropna=False):
        table_name = _text(table_name)
        if not table_name:
            errors.append("Schema review contains a blank table_name.")
            continue

        silver_names = {_text(value) for value in group["silver_table_name"] if _text(value)}
        if len(silver_names) != 1:
            errors.append(
                f"{table_name}: silver_table_name must be identical on every row; found {sorted(silver_names)}"
            )
            continue
        silver_table_name = next(iter(silver_names))

        allow_no_values = set()
        for value in group["allow_no_primary_key"]:
            try:
                allow_no_values.add(_bool(value, f"{table_name}.allow_no_primary_key"))
            except ValueError as error:
                errors.append(str(error))
        if len(allow_no_values) != 1:
            errors.append(
                f"{table_name}: allow_no_primary_key must be identical on every row."
            )
            allow_no_pk = False
        else:
            allow_no_pk = next(iter(allow_no_values))

        columns = {}
        pk_candidates = []
        foreign_keys = []
        target_names = set()

        for row_position, (_, row) in enumerate(group.iterrows(), start=1):
            source_column = _text(row["source_column"])
            target_column = _text(row["column_name"])
            datatype = _text(row["datatype"]).lower()

            if not source_column:
                errors.append(f"{table_name}: blank source_column at review row {row_position}.")
                continue
            if source_column in columns:
                errors.append(f"{table_name}: duplicate source_column '{source_column}'.")
                continue
            if not target_column:
                errors.append(f"{table_name}.{source_column}: column_name cannot be blank.")
            if target_column in target_names:
                errors.append(f"{table_name}: duplicate Silver column name '{target_column}'.")
            target_names.add(target_column)

            if datatype not in ALLOWED_DATATYPES:
                errors.append(
                    f"{table_name}.{source_column}: unsupported datatype '{datatype}'."
                )

            try:
                nullable = _bool(row["nullable"], f"{table_name}.{source_column}.nullable")
                is_pk = _bool(row["is_pk"], f"{table_name}.{source_column}.is_pk")
                is_fk = _bool(row["is_fk"], f"{table_name}.{source_column}.is_fk")
            except ValueError as error:
                errors.append(str(error))
                nullable = True
                is_pk = False
                is_fk = False

            columns[source_column] = {
                "name": target_column,
                "datatype": datatype,
                "nullable": nullable,
            }

            if is_pk:
                try:
                    order = _pk_order(row["pk_order"], row_position)
                    pk_candidates.append((order, row_position, source_column))
                except ValueError as error:
                    errors.append(f"{table_name}.{source_column}: {error}")

            fk_reference = _text(row["fk_reference"])
            if is_fk:
                if not fk_reference or "." not in fk_reference:
                    errors.append(
                        f"{table_name}.{source_column}: is_fk=true requires fk_reference='table.column'."
                    )
                else:
                    foreign_keys.append(
                        {"column": source_column, "references": fk_reference}
                    )
            elif fk_reference:
                errors.append(
                    f"{table_name}.{source_column}: fk_reference is filled but is_fk=false."
                )

        pk_candidates.sort(key=lambda item: (item[0], item[1]))
        primary_key = [item[2] for item in pk_candidates]

        if not primary_key and not allow_no_pk:
            errors.append(
                f"{table_name}: no primary key selected. Select PK column(s) or set allow_no_primary_key=true intentionally."
            )

        contract[table_name] = {
            "table": {
                "name": table_name,
                "silver_name": silver_table_name,
                "allow_no_primary_key": allow_no_pk,
                "primary_key": primary_key,
                "foreign_keys": foreign_keys,
            },
            "columns": columns,
            "quality_decisions": [],
        }

    # Validate FK parent references after all tables/columns have been compiled.
    for table_name, table_schema in contract.items():
        for fk in table_schema["table"].get("foreign_keys", []):
            parent_table, parent_column = fk["references"].split(".", 1)
            if parent_table not in contract:
                errors.append(
                    f"{table_name}.{fk['column']}: FK parent table '{parent_table}' is not in schema review."
                )
                continue
            parent_schema = contract[parent_table]
            parent_sources = set(parent_schema["columns"])
            parent_targets = {
                config["name"] for config in parent_schema["columns"].values()
            }
            if parent_column not in parent_sources and parent_column not in parent_targets:
                errors.append(
                    f"{table_name}.{fk['column']}: FK reference '{fk['references']}' cannot be resolved."
                )

    return contract, errors


# =========================================================
# DQ DECISIONS -> CONTRACT
# =========================================================


def compile_quality_decisions(
    decision_df: pd.DataFrame,
    contract: dict,
) -> list[str]:
    """Validate analyst issue decisions and attach them to their table contract."""

    required = {
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
    }
    missing = sorted(required - set(decision_df.columns))
    if missing:
        return [
            "data_quality_decisions.csv is missing columns: " + ", ".join(missing)
        ]

    errors: list[str] = []

    for _, row in decision_df.iterrows():
        issue_id = _text(row["issue_id"])
        table_name = _text(row["table_name"])
        column_name = _text(row["column_name"])
        issue_type = _text(row["issue_type"]).upper()
        decision = _text(row["decision"]).upper()
        suggested_action = _text(row["suggested_action"]).upper()
        action = _text(row["action"]).upper()
        reason = _text(row["reason"])

        if table_name not in contract:
            errors.append(f"Issue {issue_id}: unknown table '{table_name}'.")
            continue

        if not decision:
            errors.append(
                f"Issue {issue_id} ({table_name}.{column_name} {issue_type}): decision is blank. Choose FIX, KEEP, or BLOCK."
            )
            continue

        if decision not in ALLOWED_DECISIONS:
            errors.append(
                f"Issue {issue_id}: invalid decision '{decision}'. Allowed: FIX, KEEP, BLOCK."
            )
            continue

        if decision == "FIX":
            action = action or suggested_action
            if action not in ALLOWED_FIX_ACTIONS:
                errors.append(
                    f"Issue {issue_id}: FIX action '{action}' is not supported."
                )
                continue
        elif decision == "KEEP":
            action = "NONE"
            if not reason:
                errors.append(
                    f"Issue {issue_id}: KEEP requires a reason so accepted data quality exceptions are explainable."
                )
                continue
        elif decision == "BLOCK":
            action = "MANUAL_REVIEW"
            if not reason:
                errors.append(
                    f"Issue {issue_id}: BLOCK requires a reason explaining what must be investigated."
                )
                continue

        if column_name:
            table_schema = contract[table_name]
            source_columns = set(table_schema["columns"])
            target_columns = {
                config["name"] for config in table_schema["columns"].values()
            }
            # Text-variant reports can contain a real Bronze source column. Keep
            # table-level issue rows blank. Unknown columns indicate stale review data.
            if column_name not in source_columns and column_name not in target_columns:
                errors.append(
                    f"Issue {issue_id}: column '{column_name}' is not present in reviewed schema for {table_name}."
                )
                continue

        contract[table_name]["quality_decisions"].append(
            {
                "issue_id": issue_id,
                "column_name": column_name,
                "issue_type": issue_type,
                "issue_count": int(
                    0
                    if pd.isna(pd.to_numeric(row["issue_count"], errors="coerce"))
                    else pd.to_numeric(row["issue_count"], errors="coerce")
                ),
                "decision": decision,
                "action": action,
                "reason": reason,
                "example": _text(row["example"]),
                "detail": _text(row["detail"]),
            }
        )

    return errors


# =========================================================
# MAIN
# =========================================================


def main() -> None:
    """Compile both analyst CSV reviews into one validated JSON build contract."""

    print("=" * 70)
    print("SEPRO SILVER - COMPILE ANALYST REVIEW")
    print("=" * 70)

    if not SCHEMA_REVIEW_PATH.exists():
        raise FileNotFoundError(f"Missing schema review: {SCHEMA_REVIEW_PATH}")
    if not DQ_DECISIONS_PATH.exists():
        raise FileNotFoundError(f"Missing DQ decisions: {DQ_DECISIONS_PATH}")

    schema_df = pd.read_csv(SCHEMA_REVIEW_PATH, keep_default_na=False)
    decision_df = pd.read_csv(DQ_DECISIONS_PATH, keep_default_na=False)

    contract, errors = compile_schema_review(schema_df)
    errors.extend(compile_quality_decisions(decision_df, contract))

    blocked = []
    for table_name, table_schema in contract.items():
        for item in table_schema.get("quality_decisions", []):
            if item.get("decision") == "BLOCK":
                blocked.append(
                    f"{table_name}.{item.get('column_name', '')} {item.get('issue_type')}: {item.get('reason')}"
                )

    if errors:
        print("\nReview cannot be compiled:")
        for error in errors:
            print(f"- {error}")
        print("\nNo contract JSON was created.")
        raise SystemExit(1)

    REVIEW_DIR.mkdir(parents=True, exist_ok=True)
    with CONTRACT_PATH.open("w", encoding="utf-8") as file:
        json.dump(contract, file, ensure_ascii=False, indent=2)

    print(f"\nContract created: {CONTRACT_PATH}")
    if blocked:
        print("\nContract contains BLOCK decisions. build_silver.py will refuse to publish until they are resolved:")
        for item in blocked:
            print(f"- {item}")
    else:
        print("No BLOCK decisions remain. The contract is ready for build_silver.py.")


if __name__ == "__main__":
    main()
