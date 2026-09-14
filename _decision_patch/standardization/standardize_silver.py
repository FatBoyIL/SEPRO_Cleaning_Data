"""Orchestrate Landing/Bronze standardization using the reviewed schema JSON."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from config.table_config import CURRENCY_ALIASES, FX_TABLE, NULL_MARKERS
from profiling.data_quality import get_missing_masks
from standardization.landing_data.currency_standardization import (
    apply_currency_conversion,
    normalize_currency_codes,
    prepare_fx_rates,
)
from standardization.landing_data.datatype_standardization import SUPPORTED_TYPES, standardize_datatypes
from standardization.landing_data.duplicate_handling import (
    detect_pk_duplicates,
    remove_exact_duplicates,
)
from standardization.landing_data.null_standardization import standardize_null_markers
from standardization.landing_data.text_standardization import standardize_text_columns
from standardization.landing_data.value_standardization import apply_value_mappings
from standardization.decision_policy import (
    approved_fix_columns,
    blocking_decisions,
    table_action_is_fixed,
)


# =========================================================
# LOAD / VALIDATE REVIEW CONFIG
# =========================================================


def load_review_schema(path: Path) -> Dict:
    """Load the compiled analyst review contract used by the Silver build."""

    if not path.exists():
        raise FileNotFoundError(
            f"Review contract not found: {path}. Run run_review.py, complete both CSV reviews, then run review_to_json.py."
        )
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or not data:
        raise ValueError("silver_schema_review.json must contain a non-empty JSON object.")
    return data


def load_standardization_rules(path: Path) -> Dict:
    """Load optional business mappings and currency conversion rules from JSON."""

    if not path.exists():
        return {"_global": {}, "tables": {}}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError("standardization_rules.json must contain a JSON object.")
    data.setdefault("_global", {})
    data.setdefault("tables", {})
    return data


def get_silver_table_name(source_table: str, table_schema: Dict) -> str:
    """Resolve the output Silver table name, defaulting to source name without ``_raw``."""

    configured = table_schema.get("table", {}).get("silver_name")
    if configured:
        return str(configured)
    return source_table[:-4] if source_table.endswith("_raw") else source_table


def resolve_target_column(table_schema: Dict, identifier: str) -> str:
    """Resolve a source or reviewed column identifier to the final Silver column name."""

    columns = table_schema.get("columns", {})
    if identifier in columns:
        return columns[identifier]["name"]
    for source_column, config in columns.items():
        if config.get("name") == identifier:
            return config["name"]
    return identifier


def validate_review_schema_structure(
    review_schema: Dict,
    bronze_tables: Dict[str, pd.DataFrame],
) -> Tuple[List[str], List[str]]:
    """Validate the edited review JSON before any data transformation or SQL write.

    Errors block the Silver build. Warnings highlight analyst decisions such as a
    table with no reviewed PK but do not stop transformation by themselves.
    """

    errors: List[str] = []
    warnings: List[str] = []
    target_table_names: List[str] = []

    for table_name in bronze_tables:
        if table_name not in review_schema:
            errors.append(f"Missing table in review JSON: {table_name}")

    for table_name, table_schema in review_schema.items():
        if table_name not in bronze_tables:
            errors.append(f"Review JSON contains unknown Bronze table: {table_name}")
            continue

        if "table" not in table_schema or "columns" not in table_schema:
            errors.append(f"{table_name}: requires both 'table' and 'columns' sections.")
            continue

        target_table = get_silver_table_name(table_name, table_schema)
        target_table_names.append(target_table)
        source_columns = set(bronze_tables[table_name].columns)
        configs = table_schema.get("columns", {})

        missing_source_columns = source_columns - set(configs)
        if missing_source_columns:
            errors.append(
                f"{table_name}: review JSON is missing source columns {sorted(missing_source_columns)}"
            )

        target_columns = []
        for source_column, config in configs.items():
            if source_column not in source_columns:
                errors.append(f"{table_name}: unknown source column '{source_column}'.")
                continue
            target_name = config.get("name")
            datatype = str(config.get("datatype", "")).lower()
            if not target_name:
                errors.append(f"{table_name}.{source_column}: missing reviewed 'name'.")
            else:
                target_columns.append(target_name)
            if datatype not in SUPPORTED_TYPES:
                errors.append(
                    f"{table_name}.{source_column}: unsupported datatype '{datatype}'. "
                    f"Allowed: {sorted(SUPPORTED_TYPES)}"
                )
            if not isinstance(config.get("nullable"), bool):
                errors.append(f"{table_name}.{source_column}: 'nullable' must be true/false.")

        if len(target_columns) != len(set(target_columns)):
            errors.append(f"{table_name}: duplicate Silver column names after review.")

        pk = table_schema["table"].get("primary_key", [])
        allow_no_pk = bool(table_schema["table"].get("allow_no_primary_key", False))
        if not pk and not allow_no_pk:
            errors.append(
                f"{table_name}: no reviewed primary key and allow_no_primary_key is false."
            )
        elif not pk and allow_no_pk:
            warnings.append(f"{table_name}: keyless table explicitly approved by analyst.")
        for column in pk:
            resolved = resolve_target_column(table_schema, column)
            if resolved not in target_columns:
                errors.append(f"{table_name}: PK column '{column}' cannot be resolved.")

        quality_decisions = table_schema.get("quality_decisions", [])
        if not isinstance(quality_decisions, list):
            errors.append(f"{table_name}: quality_decisions must be a list.")

        for fk in table_schema["table"].get("foreign_keys", []):
            if "column" not in fk or "references" not in fk or "." not in fk["references"]:
                errors.append(f"{table_name}: invalid FK object {fk}")
                continue
            child = resolve_target_column(table_schema, fk["column"])
            if child not in target_columns:
                errors.append(f"{table_name}: FK child column '{fk['column']}' cannot be resolved.")
            parent_table, parent_column = fk["references"].split(".", 1)
            if parent_table not in review_schema:
                errors.append(f"{table_name}: FK parent table '{parent_table}' not in review JSON.")
            else:
                parent_resolved = resolve_target_column(review_schema[parent_table], parent_column)
                parent_targets = {
                    config["name"] for config in review_schema[parent_table].get("columns", {}).values()
                }
                if parent_resolved not in parent_targets:
                    errors.append(
                        f"{table_name}: FK parent column '{fk['references']}' cannot be resolved."
                    )

    if len(target_table_names) != len(set(target_table_names)):
        errors.append("Two or more Bronze tables resolve to the same Silver table name.")

    return errors, warnings


# =========================================================
# STANDARDIZE ONE / ALL TABLES
# =========================================================


def _get_table_rules(source_table: str, table_schema: Dict, rules: Dict) -> Dict:
    """Find table-specific rules using either Bronze or Silver table name."""

    table_rules = rules.get("tables", {})
    return table_rules.get(
        source_table,
        table_rules.get(get_silver_table_name(source_table, table_schema), {}),
    )


def _rename_to_reviewed_columns(df: pd.DataFrame, table_schema: Dict) -> pd.DataFrame:
    """Rename Bronze source columns to the analyst-approved Silver names."""

    rename_map = {
        source_column: config["name"]
        for source_column, config in table_schema["columns"].items()
    }
    return df.rename(columns=rename_map).copy()


def _resolve_pk_columns(table_schema: Dict) -> List[str]:
    """Resolve reviewed PK identifiers to final Silver column names."""

    return [
        resolve_target_column(table_schema, column)
        for column in table_schema.get("table", {}).get("primary_key", [])
    ]


def _approved_target_columns(
    table_schema: Dict,
    issue_type: str,
    action: str,
) -> List[str]:
    """Resolve approved source/Silver decision columns to final Silver names."""

    return sorted(
        {
            resolve_target_column(table_schema, identifier)
            for identifier in approved_fix_columns(table_schema, issue_type, action)
        }
    )


def _filter_value_mapping_rules(
    table_rules: Dict,
    approved_columns: List[str],
) -> Dict:
    """Keep semantic mappings only for columns explicitly approved with FIX."""

    filtered = dict(table_rules or {})
    mappings = filtered.get("value_mappings", {})
    filtered["value_mappings"] = {
        column: mapping
        for column, mapping in mappings.items()
        if column in set(approved_columns)
    }
    return filtered


def standardize_table(
    table_name: str,
    df: pd.DataFrame,
    table_schema: Dict,
    rules: Dict,
    fx_rates: pd.DataFrame,
) -> Tuple[pd.DataFrame, Dict, List[Dict]]:
    """Standardize one table using only analyst-approved automatic fixes.

    Schema decisions (rename/datatype/keys) always follow the compiled schema
    contract. Data-quality transformations such as fake-NULL cleanup, whitespace
    cleanup, value mapping, and exact-duplicate removal execute only when the
    corresponding issue decision is ``FIX``. ``KEEP`` leaves the issue unchanged;
    ``BLOCK`` is rejected before this function is called.
    """

    input_rows = int(len(df))
    duplicate_before = int(df.duplicated(keep=False).sum())
    result = _rename_to_reviewed_columns(df, table_schema)
    cumulative_changed = pd.Series(False, index=result.index, dtype=bool)
    issues: List[Dict] = []
    table_rules = _get_table_rules(table_name, table_schema, rules)
    global_rules = rules.get("_global", {})

    null_columns = _approved_target_columns(
        table_schema, "MISSING_VALUE", "STANDARDIZE_NULL_MARKERS"
    )
    result, changed = standardize_null_markers(
        result, NULL_MARKERS, include_columns=null_columns
    )
    cumulative_changed |= changed

    whitespace_columns = _approved_target_columns(
        table_schema, "WHITESPACE", "STANDARDIZE_TEXT"
    )
    result, changed = standardize_text_columns(
        result,
        skip_columns=table_rules.get("skip_text_columns", []),
        include_columns=whitespace_columns,
    )
    cumulative_changed |= changed

    mapping_columns = _approved_target_columns(
        table_schema, "TEXT_VARIANT", "APPLY_VALUE_MAPPING"
    )
    filtered_rules = _filter_value_mapping_rules(table_rules, mapping_columns)
    result, changed = apply_value_mappings(result, filtered_rules)
    cumulative_changed |= changed

    # Datatype conversion follows the approved schema contract. If the analyst
    # wants to keep a value that cannot parse, the reviewed datatype must be
    # changed accordingly before compiling the contract.
    result, changed, datatype_issues = standardize_datatypes(result, table_schema["columns"])
    cumulative_changed |= changed
    issues.extend(datatype_issues)

    aliases = dict(CURRENCY_ALIASES)
    aliases.update(global_rules.get("currency_aliases", {}))
    result, changed = normalize_currency_codes(result, aliases)
    cumulative_changed |= changed

    result, changed, currency_issues = apply_currency_conversion(
        result,
        table_rules=table_rules,
        fx_rates=fx_rates,
        global_rules=global_rules,
    )
    cumulative_changed |= changed
    issues.extend(currency_issues)

    changed_rows = int(cumulative_changed.sum())

    removed_duplicates = 0
    if table_action_is_fixed(
        table_schema, "EXACT_DUPLICATE", "REMOVE_EXACT_DUPLICATES"
    ):
        result, removed_duplicates = remove_exact_duplicates(result)

    pk_columns = _resolve_pk_columns(table_schema)
    pk_duplicates = detect_pk_duplicates(result, pk_columns)
    if pk_duplicates["count"]:
        issues.append(
            {
                "column_name": " + ".join(pk_columns),
                "issue_type": "PK_DUPLICATE_AFTER_STANDARDIZATION",
                "issue_count": pk_duplicates["count"],
                "example": str(pk_duplicates["sample"][:3]),
                "severity": "FAIL",
            }
        )

    for issue in issues:
        issue["table_name"] = table_name

    effective_missing_before = int(
        sum(
            (get_missing_masks(df[column])[0] | get_missing_masks(df[column])[1]).sum()
            for column in df.columns
        )
    )

    summary = {
        "table_name": table_name,
        "silver_table_name": get_silver_table_name(table_name, table_schema),
        "input_rows": input_rows,
        "output_rows": int(len(result)),
        "changed_rows": changed_rows,
        "removed_duplicates": int(removed_duplicates),
        "duplicate_before": duplicate_before,
        "duplicate_after": int(result.duplicated(keep=False).sum()),
        "null_before": effective_missing_before,
        "null_after": int(result.isna().sum().sum()),
        "standardization_issue_count": int(sum(issue["issue_count"] for issue in issues)),
    }
    return result, summary, issues


def standardize_all_tables(
    bronze_tables: Dict[str, pd.DataFrame],
    review_schema: Dict,
    rules: Dict,
) -> Tuple[Dict[str, pd.DataFrame], pd.DataFrame, pd.DataFrame]:
    """Standardize every reviewed Bronze table and return data, summary, and issues."""

    blocked = blocking_decisions(review_schema)
    if blocked:
        details = "; ".join(
            f"{item['table_name']}.{item.get('column_name', '')} {item.get('issue_type')}: {item.get('reason', '')}"
            for item in blocked
        )
        raise ValueError(
            "Silver build is blocked by analyst decisions. Resolve BLOCK rows before building. "
            + details
        )

    global_rules = rules.get("_global", {})
    aliases = dict(CURRENCY_ALIASES)
    aliases.update(global_rules.get("currency_aliases", {}))
    fx_rates = prepare_fx_rates(bronze_tables.get(FX_TABLE), aliases=aliases)

    silver_tables: Dict[str, pd.DataFrame] = {}
    summary_rows: List[Dict] = []
    issue_rows: List[Dict] = []

    for table_name, table_schema in review_schema.items():
        standardized, summary, issues = standardize_table(
            table_name=table_name,
            df=bronze_tables[table_name],
            table_schema=table_schema,
            rules=rules,
            fx_rates=fx_rates,
        )
        silver_tables[table_name] = standardized
        summary_rows.append(summary)
        issue_rows.extend(issues)

    summary_df = pd.DataFrame(summary_rows)
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
    return silver_tables, summary_df, issues_df
