"""Generate a reviewable SQL Server script for the Silver Layer.

Workflow:
    run_review.py
        -> analyst edits review CSV files
        -> review_to_json.py
        -> build_silver.py
        -> output/build_silver.sql
        -> analyst reviews and manually executes SQL in SSMS

IMPORTANT
---------
This Python file NEVER opens a database connection and NEVER writes to SQL Server.
It compiles the reviewed JSON contract + standardization rules into deterministic
T-SQL. Database execution is a separate, manual approval step.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from config.table_config import (
    BASE_CURRENCY,
    BRONZE_SCHEMA,
    CURRENCY_ALIASES,
    FX_CURRENCY_COLUMN,
    FX_DATE_COLUMN,
    FX_RATE_COLUMN,
    FX_TABLE,
    NULL_MARKERS,
    SILVER_SCHEMA,
)

BASE_DIR = Path(__file__).resolve().parent
REVIEW_PATH = BASE_DIR / "review" / "silver_review_contract.json"
RULES_PATH = BASE_DIR / "standardization" / "rules" / "standardization_rules.json"
OUTPUT_DIR = BASE_DIR / "output"
SQL_OUTPUT_PATH = OUTPUT_DIR / "build_silver.sql"

LOGICAL_TO_SQL = {
    "string": "NVARCHAR(4000)",
    "integer": "BIGINT",
    "decimal": "DECIMAL(38,10)",
    "date": "DATE",
    "datetime": "DATETIME2",
    "boolean": "BIT",
}


# =========================================================
# LOAD CONTRACT / RULES
# =========================================================


def load_json(path: Path, *, required: bool = True) -> dict:
    """Load one JSON object from disk without touching any database."""

    if not path.exists():
        if required:
            raise FileNotFoundError(f"Missing required file: {path}")
        return {}
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise ValueError(f"{path.name} must contain a JSON object.")
    return data


def get_silver_table_name(source_table: str, table_schema: dict) -> str:
    """Resolve the reviewed Silver table name, defaulting to source without _raw."""

    configured = table_schema.get("table", {}).get("silver_name")
    if configured:
        return str(configured)
    return source_table[:-4] if source_table.endswith("_raw") else source_table


def resolve_target_column(table_schema: dict, identifier: str) -> str:
    """Resolve a Bronze source or Silver alias to the final Silver column name."""

    columns = table_schema.get("columns", {})
    if identifier in columns:
        return str(columns[identifier].get("name", identifier))
    for source_column, config in columns.items():
        if str(config.get("name", "")) == str(identifier):
            return str(config["name"])
    return str(identifier)


def get_table_rules(source_table: str, table_schema: dict, rules: dict) -> dict:
    """Return table-specific business rules using Bronze or Silver table name."""

    table_rules = rules.get("tables", {})
    silver_name = get_silver_table_name(source_table, table_schema)
    value = table_rules.get(source_table, table_rules.get(silver_name, {}))
    return value if isinstance(value, dict) else {}


# =========================================================
# SQL HELPERS
# =========================================================


def q(identifier: str) -> str:
    """Bracket-quote a SQL Server identifier."""

    return "[" + str(identifier).replace("]", "]]" ) + "]"


def qualified(schema: str, table: str) -> str:
    """Return a bracket-quoted two-part SQL Server object name."""

    return f"{q(schema)}.{q(table)}"


def sql_nvarchar(value: Any) -> str:
    """Return a safely quoted Unicode SQL string literal."""

    text = "" if value is None else str(value)
    return "N'" + text.replace("'", "''") + "'"


def sql_comment(value: Any) -> str:
    """Make arbitrary analyst text safe inside a single-line SQL comment."""

    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def constraint_name(prefix: str, *parts: str) -> str:
    """Build a stable SQL Server constraint name no longer than 128 characters."""

    raw = "_".join([prefix, *[str(part) for part in parts]])
    cleaned = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)
    if len(cleaned) <= 120:
        return cleaned
    digest = hashlib.sha1(cleaned.encode("utf-8")).hexdigest()[:8]
    return cleaned[:111] + "_" + digest


def indent_sql(sql: str, spaces: int = 8) -> str:
    """Indent a multi-line SQL fragment for readable generated output."""

    prefix = " " * spaces
    return "\n".join(prefix + line if line else line for line in sql.splitlines())


def decision_rows(table_schema: dict) -> list[dict]:
    """Return normalized Data Quality decision rows from one table contract."""

    rows = table_schema.get("quality_decisions", [])
    return rows if isinstance(rows, list) else []


def blocking_decisions(review_schema: dict) -> list[dict]:
    """Return BLOCK decisions; SQL generation is refused while any remain."""

    result: list[dict] = []
    for table_name, table_schema in review_schema.items():
        for row in decision_rows(table_schema):
            if str(row.get("decision", "")).upper() != "BLOCK":
                continue
            item = dict(row)
            item["table_name"] = table_name
            result.append(item)
    return result


def approved_fix_columns(table_schema: dict, issue_type: str, action: str) -> set[str]:
    """Return source/Silver column identifiers approved for a specific FIX action."""

    issue_type = issue_type.upper()
    action = action.upper()
    result: set[str] = set()
    for row in decision_rows(table_schema):
        if str(row.get("decision", "")).upper() != "FIX":
            continue
        if str(row.get("issue_type", "")).upper() != issue_type:
            continue
        if str(row.get("action", "")).upper() != action:
            continue
        identifier = str(row.get("column_name", "")).strip()
        if identifier:
            result.add(identifier)
    return result


def table_fix_enabled(table_schema: dict, issue_type: str, action: str) -> bool:
    """Return True for an approved table-level FIX/action pair."""

    for row in decision_rows(table_schema):
        if str(row.get("decision", "")).upper() != "FIX":
            continue
        if str(row.get("issue_type", "")).upper() != issue_type.upper():
            continue
        if str(row.get("action", "")).upper() != action.upper():
            continue
        if not str(row.get("column_name", "")).strip():
            return True
    return False


def is_approved_column(
    table_schema: dict,
    source_column: str,
    target_column: str,
    issue_type: str,
    action: str,
) -> bool:
    """Check whether a source/target column is included in one approved FIX rule."""

    identifiers = approved_fix_columns(table_schema, issue_type, action)
    return source_column in identifiers or target_column in identifiers


# =========================================================
# STATIC CONTRACT VALIDATION
# =========================================================


def validate_contract(review_schema: dict, rules: dict) -> list[str]:
    """Validate everything that can be checked without connecting to SQL Server."""

    errors: list[str] = []
    if not review_schema:
        return ["Review contract is empty."]

    silver_names: list[str] = []

    for table_name, table_schema in review_schema.items():
        table = table_schema.get("table", {})
        columns = table_schema.get("columns", {})
        if not isinstance(columns, dict) or not columns:
            errors.append(f"{table_name}: columns must be a non-empty object.")
            continue

        silver_name = get_silver_table_name(table_name, table_schema)
        silver_names.append(silver_name)

        target_names: list[str] = []
        for source_column, config in columns.items():
            target = str(config.get("name", "")).strip()
            datatype = str(config.get("datatype", "")).lower()
            nullable = config.get("nullable")
            if not source_column:
                errors.append(f"{table_name}: blank source column.")
            if not target:
                errors.append(f"{table_name}.{source_column}: missing Silver column name.")
            target_names.append(target)
            if datatype not in LOGICAL_TO_SQL:
                errors.append(
                    f"{table_name}.{source_column}: unsupported datatype '{datatype}'."
                )
            if not isinstance(nullable, bool):
                errors.append(
                    f"{table_name}.{source_column}: nullable must be true/false."
                )

        if len(target_names) != len(set(target_names)):
            errors.append(f"{table_name}: duplicate Silver column names.")

        pk = table.get("primary_key", [])
        allow_no_pk = bool(table.get("allow_no_primary_key", False))
        if not pk and not allow_no_pk:
            errors.append(
                f"{table_name}: no primary key selected and allow_no_primary_key=false."
            )
        for identifier in pk:
            resolved = resolve_target_column(table_schema, identifier)
            if resolved not in target_names:
                errors.append(f"{table_name}: PK '{identifier}' cannot be resolved.")

        table_rules = get_table_rules(table_name, table_schema, rules)
        mappings = table_rules.get("value_mappings", {})
        for row in decision_rows(table_schema):
            if str(row.get("decision", "")).upper() != "FIX":
                continue
            if str(row.get("action", "")).upper() != "APPLY_VALUE_MAPPING":
                continue
            identifier = str(row.get("column_name", "")).strip()
            target = resolve_target_column(table_schema, identifier)
            if target not in mappings:
                errors.append(
                    f"{table_name}.{identifier}: APPLY_VALUE_MAPPING requires "
                    f"a value_mappings rule for Silver column '{target}'."
                )

    if len(silver_names) != len(set(silver_names)):
        errors.append("Two or more source tables resolve to the same Silver table name.")

    for table_name, table_schema in review_schema.items():
        for fk in table_schema.get("table", {}).get("foreign_keys", []):
            child = str(fk.get("column", ""))
            reference = str(fk.get("references", ""))
            if "." not in reference:
                errors.append(f"{table_name}.{child}: invalid FK reference '{reference}'.")
                continue
            parent_table, parent_column = reference.split(".", 1)
            if parent_table not in review_schema:
                errors.append(
                    f"{table_name}.{child}: parent table '{parent_table}' is not in the contract."
                )
                continue
            child_target = resolve_target_column(table_schema, child)
            parent_target = resolve_target_column(review_schema[parent_table], parent_column)
            child_targets = {
                str(config.get("name", ""))
                for config in table_schema.get("columns", {}).values()
            }
            parent_targets = {
                str(config.get("name", ""))
                for config in review_schema[parent_table].get("columns", {}).values()
            }
            if child_target not in child_targets:
                errors.append(f"{table_name}: FK child '{child}' cannot be resolved.")
            if parent_target not in parent_targets:
                errors.append(
                    f"{table_name}: FK parent '{reference}' cannot be resolved."
                )
                continue

            # The current review contract represents one FK column at a time.
            # To create a physical SQL Server FK safely, the referenced parent
            # column must therefore be the parent's single-column reviewed PK.
            parent_pk = [
                resolve_target_column(review_schema[parent_table], identifier)
                for identifier in review_schema[parent_table].get("table", {}).get("primary_key", [])
            ]
            if parent_pk != [parent_target]:
                errors.append(
                    f"{table_name}.{child}: FK parent '{reference}' is not the parent's "
                    "single-column reviewed PK. Composite FK relationships are not yet "
                    "represented by the review contract."
                )

    return errors


# =========================================================
# COLUMN TRANSFORMATION COMPILER
# =========================================================


def fake_null_sql(expr: str) -> str:
    """Compile configured fake-NULL markers to a SQL CASE expression."""

    markers = sorted({str(value).strip().lower() for value in NULL_MARKERS})
    literals = ", ".join(sql_nvarchar(value) for value in markers)
    normalized = f"LOWER(LTRIM(RTRIM(CONVERT(NVARCHAR(4000), {expr}))))"
    return f"CASE WHEN {normalized} IN ({literals}) THEN NULL ELSE {expr} END"


def normalize_text_sql(expr: str) -> str:
    """Trim text, replace tabs/newlines, and collapse repeated spaces in T-SQL."""

    current = f"CONVERT(NVARCHAR(4000), {expr})"
    current = f"REPLACE({current}, CHAR(9), N' ')"
    current = f"REPLACE({current}, CHAR(13), N' ')"
    current = f"REPLACE({current}, CHAR(10), N' ')"
    # Six passes collapse runs up to 64 spaces without regex dependencies.
    for _ in range(6):
        current = f"REPLACE({current}, N'  ', N' ')"
    return f"LTRIM(RTRIM({current}))"


def mapping_sql(expr: str, mapping: dict) -> str:
    """Compile a case-insensitive analyst value mapping into a CASE expression."""

    normalized = f"LOWER(LTRIM(RTRIM(CONVERT(NVARCHAR(4000), {expr}))))"
    lines = ["CASE"]
    for source, target in mapping.items():
        source_key = str(source).strip().lower()
        lines.append(
            f"    WHEN {normalized} = {sql_nvarchar(source_key)} THEN {sql_nvarchar(target)}"
        )
    lines.append(f"    ELSE {expr}")
    lines.append("END")
    return "\n".join(lines)


def currency_alias_sql(expr: str, aliases: dict) -> str:
    """Normalize configured currency aliases while preserving unknown codes."""

    if not aliases:
        return expr
    normalized = f"UPPER(LTRIM(RTRIM(CONVERT(NVARCHAR(50), {expr}))))"
    lines = ["CASE"]
    for source, target in sorted(aliases.items(), key=lambda item: str(item[0])):
        lines.append(
            f"    WHEN {normalized} = UPPER({sql_nvarchar(source)}) THEN {sql_nvarchar(str(target).upper())}"
        )
    lines.append(f"    ELSE {normalized}")
    lines.append("END")
    return "\n".join(lines)


def is_currency_column(column: str) -> bool:
    """Match the same obvious currency-code column pattern used by standardization."""

    return (
        column in {"currency", "currency_code"}
        or (column.endswith("_currency") and not column.endswith("_per_currency"))
        or column.endswith("_currency_code")
    )


def numeric_clean_sql(expr: str) -> str:
    """Compile the reviewed numeric parser: trim, remove commas, support (123) negatives."""

    text_expr = f"LTRIM(RTRIM(CONVERT(NVARCHAR(4000), {expr})))"
    return (
        "CASE "
        f"WHEN LEFT({text_expr}, 1) = N'(' AND RIGHT({text_expr}, 1) = N')' "
        f"THEN N'-' + REPLACE(SUBSTRING({text_expr}, 2, LEN({text_expr}) - 2), N',', N'') "
        f"ELSE REPLACE({text_expr}, N',', N'') END"
    )


def cast_sql(expr: str, datatype: str) -> str:
    """Compile one reviewed logical datatype to a safe SQL Server conversion."""

    datatype = datatype.lower()
    if datatype == "string":
        return f"TRY_CONVERT(NVARCHAR(4000), {expr})"
    if datatype == "decimal":
        return f"TRY_CONVERT(DECIMAL(38,10), {numeric_clean_sql(expr)})"
    if datatype == "integer":
        decimal_expr = f"TRY_CONVERT(DECIMAL(38,10), {numeric_clean_sql(expr)})"
        return (
            "CASE "
            f"WHEN {decimal_expr} IS NULL THEN NULL "
            f"WHEN {decimal_expr} = FLOOR({decimal_expr}) "
            f"THEN TRY_CONVERT(BIGINT, {decimal_expr}) "
            "ELSE NULL END"
        )
    if datatype == "date":
        return f"TRY_CONVERT(DATE, {expr})"
    if datatype == "datetime":
        return f"TRY_CONVERT(DATETIME2, {expr})"
    if datatype == "boolean":
        normalized = f"LOWER(LTRIM(RTRIM(CONVERT(NVARCHAR(50), {expr}))))"
        return (
            "CASE "
            f"WHEN {normalized} IN (N'true', N'1', N'yes', N'y', N't') THEN CAST(1 AS BIT) "
            f"WHEN {normalized} IN (N'false', N'0', N'no', N'n', N'f') THEN CAST(0 AS BIT) "
            "ELSE NULL END"
        )
    raise ValueError(f"Unsupported datatype: {datatype}")


def compile_base_column_expression(
    source_column: str,
    config: dict,
    table_schema: dict,
    table_rules: dict,
    global_rules: dict,
) -> tuple[str, str]:
    """Return (pre-cast expression, final cast expression) for one reviewed column."""

    target_column = str(config["name"])
    expr = f"src.{q(source_column)}"

    if is_approved_column(
        table_schema,
        source_column,
        target_column,
        "MISSING_VALUE",
        "STANDARDIZE_NULL_MARKERS",
    ):
        expr = fake_null_sql(expr)

    if is_approved_column(
        table_schema,
        source_column,
        target_column,
        "WHITESPACE",
        "STANDARDIZE_TEXT",
    ):
        expr = normalize_text_sql(expr)

    if is_approved_column(
        table_schema,
        source_column,
        target_column,
        "TEXT_VARIANT",
        "APPLY_VALUE_MAPPING",
    ):
        mapping = table_rules.get("value_mappings", {}).get(target_column, {})
        expr = mapping_sql(expr, mapping)

    if is_currency_column(target_column):
        aliases = dict(CURRENCY_ALIASES)
        aliases.update(global_rules.get("currency_aliases", {}))
        expr = currency_alias_sql(expr, aliases)

    pre_cast = expr
    final = cast_sql(expr, str(config["datatype"]))
    return pre_cast, final


def target_expression_map(
    source_table: str,
    table_schema: dict,
    rules: dict,
) -> dict[str, dict[str, str]]:
    """Compile reviewed base-column expressions keyed by final Silver column name."""

    table_rules = get_table_rules(source_table, table_schema, rules)
    global_rules = rules.get("_global", {})
    expressions: dict[str, dict[str, str]] = {}
    for source_column, config in table_schema.get("columns", {}).items():
        pre_cast, final = compile_base_column_expression(
            source_column,
            config,
            table_schema,
            table_rules,
            global_rules,
        )
        target = str(config["name"])
        expressions[target] = {
            "source_column": source_column,
            "pre_cast": pre_cast,
            "final": final,
            "datatype": str(config["datatype"]).lower(),
            "nullable": bool(config["nullable"]),
        }
    return expressions


# =========================================================
# CURRENCY DERIVED COLUMNS
# =========================================================


def fx_rate_sql(
    currency_expr: str,
    date_expr: str,
    global_rules: dict,
    currency_rules: dict,
) -> str:
    """Compile one scalar FX-rate lookup against the Bronze FX table."""

    base_currency = str(global_rules.get("base_currency", BASE_CURRENCY)).upper()
    missing_rule = str(
        currency_rules.get(
            "missing_fx_rate_rule",
            global_rules.get("missing_fx_rate_rule", "previous_available_rate"),
        )
    )

    aliases = dict(CURRENCY_ALIASES)
    aliases.update(global_rules.get("currency_aliases", {}))
    fx_currency_expr = currency_alias_sql(f"fx.{q(FX_CURRENCY_COLUMN)}", aliases)
    txn_currency_expr = f"UPPER(CONVERT(NVARCHAR(50), {currency_expr}))"
    txn_date_expr = f"TRY_CONVERT(DATE, {date_expr})"
    fx_date_expr = f"TRY_CONVERT(DATE, fx.{q(FX_DATE_COLUMN)})"

    if missing_rule == "previous_available_rate":
        date_predicate = f"{fx_date_expr} <= {txn_date_expr}"
        ordering = f"ORDER BY {fx_date_expr} DESC"
    else:
        date_predicate = f"{fx_date_expr} = {txn_date_expr}"
        ordering = ""

    lookup = (
        "(SELECT TOP (1) TRY_CONVERT(DECIMAL(38,10), fx."
        + q(FX_RATE_COLUMN)
        + ")\n"
        + f" FROM {qualified(BRONZE_SCHEMA, FX_TABLE)} AS fx\n"
        + f" WHERE UPPER(CONVERT(NVARCHAR(50), {fx_currency_expr})) = {txn_currency_expr}\n"
        + f"   AND {date_predicate}"
        + (f"\n {ordering}" if ordering else "")
        + ")"
    )

    return (
        "CASE "
        f"WHEN {txn_currency_expr} = {sql_nvarchar(base_currency)} THEN CAST(1 AS DECIMAL(38,10)) "
        f"ELSE {lookup} END"
    )


def compile_currency_outputs(
    source_table: str,
    table_schema: dict,
    rules: dict,
    expressions: dict[str, dict[str, str]],
) -> dict[str, dict[str, str]]:
    """Compile opt-in VND conversion output/rate columns from standardization rules."""

    table_rules = get_table_rules(source_table, table_schema, rules)
    global_rules = rules.get("_global", {})
    currency_rules = table_rules.get("currency", {})
    conversions = currency_rules.get("conversions", [])
    derived: dict[str, dict[str, str]] = {}

    for conversion in conversions:
        amount_col = str(conversion["amount_column"])
        currency_col = str(conversion["currency_column"])
        date_col = str(conversion["date_column"])
        output_col = str(conversion["output_column"])
        rate_col = str(conversion.get("rate_output_column", "fx_rate_to_vnd"))

        missing = [
            column
            for column in (amount_col, currency_col, date_col)
            if column not in expressions
        ]
        if missing:
            raise ValueError(
                f"{source_table}: currency conversion references missing Silver column(s): {missing}"
            )

        amount_expr = expressions[amount_col]["final"]
        currency_expr = expressions[currency_col]["final"]
        date_expr = expressions[date_col]["final"]
        rate_expr = fx_rate_sql(
            currency_expr,
            date_expr,
            global_rules,
            currency_rules,
        )
        output_expr = (
            f"TRY_CONVERT(DECIMAL(38,10), {amount_expr}) * ({rate_expr})"
        )

        for candidate in (rate_col, output_col):
            if candidate in derived:
                raise ValueError(
                    f"{source_table}: duplicate derived currency column '{candidate}'. "
                    "Use unique output/rate column names for each conversion."
                )

        derived[rate_col] = {
            "final": rate_expr,
            "sql_type": "DECIMAL(38,10)",
            "nullable": True,
            "kind": "fx_rate",
            "amount_expr": amount_expr,
            "currency_expr": currency_expr,
        }
        derived[output_col] = {
            "final": output_expr,
            "sql_type": "DECIMAL(38,10)",
            "nullable": True,
            "kind": "currency_output",
            "rate_expr": rate_expr,
            "amount_expr": amount_expr,
            "currency_expr": currency_expr,
        }

    return derived


# =========================================================
# TABLE SQL COMPILER
# =========================================================


def transformed_select_sql(
    source_table: str,
    table_schema: dict,
    rules: dict,
) -> tuple[str, dict[str, dict[str, str]], dict[str, dict[str, str]]]:
    """Compile the reusable transformed dataset SELECT for one Silver table."""

    expressions = target_expression_map(source_table, table_schema, rules)
    derived = compile_currency_outputs(
        source_table,
        table_schema,
        rules,
        expressions,
    )

    select_items: list[str] = []
    for target, metadata in expressions.items():
        select_items.append(f"{metadata['final']} AS {q(target)}")
    for target, metadata in derived.items():
        if target in expressions:
            raise ValueError(
                f"{source_table}: derived currency column '{target}' conflicts with reviewed schema."
            )
        select_items.append(f"{metadata['final']} AS {q(target)}")

    distinct = "DISTINCT " if table_fix_enabled(
        table_schema,
        "EXACT_DUPLICATE",
        "REMOVE_EXACT_DUPLICATES",
    ) else ""

    source_object = qualified(BRONZE_SCHEMA, source_table)
    sql = (
        f"SELECT {distinct}\n"
        + ",\n".join("    " + item.replace("\n", "\n    ") for item in select_items)
        + f"\nFROM {source_object} AS src"
    )
    return sql, expressions, derived


def create_table_sql(
    source_table: str,
    table_schema: dict,
    derived: dict[str, dict[str, str]],
) -> str:
    """Compile an explicit CREATE TABLE using analyst-reviewed logical datatypes."""

    silver_name = get_silver_table_name(source_table, table_schema)
    definitions: list[str] = []
    for _, config in table_schema.get("columns", {}).items():
        target = str(config["name"])
        sql_type = LOGICAL_TO_SQL[str(config["datatype"]).lower()]
        nullability = "NULL" if bool(config["nullable"]) else "NOT NULL"
        definitions.append(f"    {q(target)} {sql_type} {nullability}")

    for target, metadata in derived.items():
        definitions.append(
            f"    {q(target)} {metadata['sql_type']} NULL"
        )

    target_object = qualified(SILVER_SCHEMA, silver_name)
    return (
        f"CREATE TABLE {target_object} (\n"
        + ",\n".join(definitions)
        + "\n);"
    )


def target_exists_guard_sql(source_table: str, table_schema: dict) -> str:
    """Generate a non-destructive guard: never silently replace an existing Silver table."""

    silver_name = get_silver_table_name(source_table, table_schema)
    object_name = f"{SILVER_SCHEMA}.{silver_name}"
    message = (
        f"Target table {object_name} already exists. Review/drop/rename it manually before rebuild."
    )
    return (
        f"IF OBJECT_ID({sql_nvarchar(object_name)}, N'U') IS NOT NULL\n"
        f"    THROW 51000, {sql_nvarchar(message)}, 1;"
    )


def schema_runtime_guards_sql(
    source_table: str,
    table_schema: dict,
    transformed_select: str,
    expressions: dict[str, dict[str, str]],
    derived: dict[str, dict[str, str]],
) -> list[str]:
    """Compile runtime guards that stop manual SQL execution on hard schema failures."""

    checks: list[str] = []
    source_object = qualified(BRONZE_SCHEMA, source_table)

    # Datatype parse failures are checked against the pre-cast expression so
    # analyst-approved fake NULL conversion is not mistaken for a parse error.
    for target, metadata in expressions.items():
        if metadata["datatype"] == "string":
            continue
        pre_cast = metadata["pre_cast"]
        final = metadata["final"]
        message = f"Datatype conversion failed: {source_table}.{target}"
        checks.append(
            "IF EXISTS (\n"
            f"    SELECT 1 FROM {source_object} AS src\n"
            f"    WHERE ({pre_cast}) IS NOT NULL AND ({final}) IS NULL\n"
            ")\n"
            f"    THROW 51001, {sql_nvarchar(message)}, 1;"
        )

    # Missing FX rate checks for configured conversion outputs.
    seen_rate_exprs: set[str] = set()
    for target, metadata in derived.items():
        if metadata.get("kind") != "currency_output":
            continue
        rate_expr = metadata["rate_expr"]
        if rate_expr in seen_rate_exprs:
            continue
        seen_rate_exprs.add(rate_expr)
        amount_expr = metadata["amount_expr"]
        currency_expr = metadata["currency_expr"]
        message = f"Missing FX rate while generating {source_table}.{target}"
        checks.append(
            "IF EXISTS (\n"
            f"    SELECT 1 FROM {source_object} AS src\n"
            f"    WHERE ({amount_expr}) IS NOT NULL\n"
            f"      AND ({currency_expr}) IS NOT NULL\n"
            f"      AND ({rate_expr}) IS NULL\n"
            ")\n"
            f"    THROW 51002, {sql_nvarchar(message)}, 1;"
        )

    # NOT NULL checks use the fully transformed dataset.
    required_columns = [
        str(config["name"])
        for config in table_schema.get("columns", {}).values()
        if not bool(config["nullable"])
    ]
    for target in required_columns:
        message = f"NOT NULL validation failed: {source_table}.{target}"
        checks.append(
            "IF EXISTS (\n"
            "    SELECT 1\n"
            "    FROM (\n"
            + indent_sql(transformed_select, 8)
            + "\n    ) AS transformed\n"
            f"    WHERE transformed.{q(target)} IS NULL\n"
            ")\n"
            f"    THROW 51003, {sql_nvarchar(message)}, 1;"
        )

    # Reviewed PK must be non-null and unique after transformations / approved dedupe.
    pk_targets = [
        resolve_target_column(table_schema, identifier)
        for identifier in table_schema.get("table", {}).get("primary_key", [])
    ]
    if pk_targets:
        null_predicate = " OR ".join(f"transformed.{q(col)} IS NULL" for col in pk_targets)
        message = f"Primary key contains NULL: {source_table} ({', '.join(pk_targets)})"
        checks.append(
            "IF EXISTS (\n"
            "    SELECT 1\n"
            "    FROM (\n"
            + indent_sql(transformed_select, 8)
            + "\n    ) AS transformed\n"
            f"    WHERE {null_predicate}\n"
            ")\n"
            f"    THROW 51004, {sql_nvarchar(message)}, 1;"
        )

        group_by = ", ".join(f"transformed.{q(col)}" for col in pk_targets)
        message = f"Primary key duplicate after transformation: {source_table} ({', '.join(pk_targets)})"
        checks.append(
            "IF EXISTS (\n"
            "    SELECT 1\n"
            "    FROM (\n"
            + indent_sql(transformed_select, 8)
            + "\n    ) AS transformed\n"
            f"    GROUP BY {group_by}\n"
            "    HAVING COUNT_BIG(*) > 1\n"
            ")\n"
            f"    THROW 51005, {sql_nvarchar(message)}, 1;"
        )

    return checks


def insert_sql(
    source_table: str,
    table_schema: dict,
    transformed_select: str,
    derived: dict[str, dict[str, str]],
) -> str:
    """Compile INSERT INTO Silver from the transformed Bronze SELECT."""

    silver_name = get_silver_table_name(source_table, table_schema)
    targets = [str(config["name"]) for config in table_schema.get("columns", {}).values()]
    targets.extend(derived.keys())
    target_list = ",\n".join("    " + q(column) for column in targets)
    return (
        f"INSERT INTO {qualified(SILVER_SCHEMA, silver_name)} (\n"
        + target_list
        + "\n)\n"
        + transformed_select
        + ";"
    )


def primary_key_sql(source_table: str, table_schema: dict) -> str | None:
    """Compile the reviewed PK as a physical SQL Server constraint."""

    pk = table_schema.get("table", {}).get("primary_key", [])
    if not pk:
        return None
    silver_name = get_silver_table_name(source_table, table_schema)
    targets = [resolve_target_column(table_schema, identifier) for identifier in pk]
    name = constraint_name("PK", silver_name, *targets)
    columns = ", ".join(q(column) for column in targets)
    return (
        f"ALTER TABLE {qualified(SILVER_SCHEMA, silver_name)}\n"
        f"ADD CONSTRAINT {q(name)} PRIMARY KEY ({columns});"
    )


def foreign_key_records(review_schema: dict) -> list[dict]:
    """Resolve all reviewed foreign keys to final Silver table/column names."""

    records: list[dict] = []
    for child_source, child_schema in review_schema.items():
        child_table = get_silver_table_name(child_source, child_schema)
        for fk in child_schema.get("table", {}).get("foreign_keys", []):
            child_column = resolve_target_column(child_schema, str(fk["column"]))
            parent_source, parent_identifier = str(fk["references"]).split(".", 1)
            parent_schema = review_schema[parent_source]
            parent_table = get_silver_table_name(parent_source, parent_schema)
            parent_column = resolve_target_column(parent_schema, parent_identifier)
            records.append(
                {
                    "child_source": child_source,
                    "child_table": child_table,
                    "child_column": child_column,
                    "parent_source": parent_source,
                    "parent_table": parent_table,
                    "parent_column": parent_column,
                }
            )
    return records


def foreign_key_sql(record: dict) -> str:
    """Compile one reviewed FK as a physical SQL Server relationship."""

    name = constraint_name(
        "FK",
        record["child_table"],
        record["child_column"],
        record["parent_table"],
    )
    return (
        f"ALTER TABLE {qualified(SILVER_SCHEMA, record['child_table'])}\n"
        f"ADD CONSTRAINT {q(name)} FOREIGN KEY ({q(record['child_column'])})\n"
        f"REFERENCES {qualified(SILVER_SCHEMA, record['parent_table'])} ({q(record['parent_column'])});"
    )


# =========================================================
# DOCUMENTATION / VALIDATION SQL
# =========================================================


def relationship_summary_comments(review_schema: dict) -> str:
    """Render proposed/reviewed PK-FK relationships at the top of the SQL file."""

    records = foreign_key_records(review_schema)
    lines = [
        "/* ================================================================",
        "   REVIEWED / PROPOSED SILVER RELATIONSHIPS",
        "   ---------------------------------------------------------------",
    ]
    if not records:
        lines.append("   No foreign-key relationships were approved in the review contract.")
    else:
        for index, record in enumerate(records, start=1):
            lines.append(
                f"   {index}. {SILVER_SCHEMA}.{record['parent_table']}.{record['parent_column']}"
            )
            lines.append(
                f"      1  --------------------  *  {SILVER_SCHEMA}.{record['child_table']}.{record['child_column']}"
            )
    lines.append("   ================================================================ */")
    return "\n".join(lines)


def analyst_decision_comments(source_table: str, table_schema: dict) -> str:
    """Document FIX/KEEP analyst decisions next to generated table SQL."""

    rows = decision_rows(table_schema)
    if not rows:
        return "-- No Data Quality decisions were attached to this table."
    lines = ["-- Analyst Data Quality decisions:"]
    for row in rows:
        decision = str(row.get("decision", "")).upper()
        issue = str(row.get("issue_type", ""))
        column = str(row.get("column_name", "")) or "<table>"
        action = str(row.get("action", ""))
        reason = sql_comment(row.get("reason", ""))
        detail = f" | reason={reason}" if reason else ""
        lines.append(
            f"--   {decision}: {issue} @ {column} | action={action or 'NONE'}{detail}"
        )
    return "\n".join(lines)


def final_validation_queries(review_schema: dict) -> str:
    """Generate read-only post-build checks that the analyst can inspect in SSMS."""

    chunks: list[str] = [
        "/* ================================================================",
        "   POST-BUILD VALIDATION QUERIES",
        "   These statements are read-only and are intentionally left visible",
        "   so the analyst can inspect the result after execution.",
        "   ================================================================ */",
    ]

    for source_table, table_schema in review_schema.items():
        silver_name = get_silver_table_name(source_table, table_schema)
        obj = qualified(SILVER_SCHEMA, silver_name)
        chunks.append(f"\n-- Row count: {SILVER_SCHEMA}.{silver_name}")
        chunks.append(f"SELECT COUNT_BIG(*) AS row_count FROM {obj};")

        pk_targets = [
            resolve_target_column(table_schema, identifier)
            for identifier in table_schema.get("table", {}).get("primary_key", [])
        ]
        if pk_targets:
            columns = ", ".join(q(column) for column in pk_targets)
            chunks.append(f"-- PK duplicate check: {SILVER_SCHEMA}.{silver_name}")
            chunks.append(
                f"SELECT {columns}, COUNT_BIG(*) AS duplicate_count\n"
                f"FROM {obj}\n"
                f"GROUP BY {columns}\n"
                "HAVING COUNT_BIG(*) > 1;"
            )

    for record in foreign_key_records(review_schema):
        child = qualified(SILVER_SCHEMA, record["child_table"])
        parent = qualified(SILVER_SCHEMA, record["parent_table"])
        ccol = q(record["child_column"])
        pcol = q(record["parent_column"])
        chunks.append(
            f"\n-- FK orphan check: {record['child_table']}.{record['child_column']} "
            f"-> {record['parent_table']}.{record['parent_column']}"
        )
        chunks.append(
            "SELECT COUNT_BIG(*) AS orphan_count\n"
            f"FROM {child} AS c\n"
            f"LEFT JOIN {parent} AS p ON c.{ccol} = p.{pcol}\n"
            f"WHERE c.{ccol} IS NOT NULL AND p.{pcol} IS NULL;"
        )

    chunks.append(
        "\n-- Physical FK relationships created in Silver\n"
        "SELECT\n"
        "    fk.name AS foreign_key_name,\n"
        "    OBJECT_SCHEMA_NAME(fk.parent_object_id) AS child_schema,\n"
        "    OBJECT_NAME(fk.parent_object_id) AS child_table,\n"
        "    OBJECT_SCHEMA_NAME(fk.referenced_object_id) AS parent_schema,\n"
        "    OBJECT_NAME(fk.referenced_object_id) AS parent_table\n"
        "FROM sys.foreign_keys AS fk\n"
        f"WHERE OBJECT_SCHEMA_NAME(fk.parent_object_id) = {sql_nvarchar(SILVER_SCHEMA)}\n"
        "ORDER BY child_table, foreign_key_name;"
    )
    return "\n".join(chunks)


# =========================================================
# FULL SQL GENERATOR
# =========================================================


def generate_sql(review_schema: dict, rules: dict) -> str:
    """Compile the complete, human-reviewable Silver T-SQL script."""

    errors = validate_contract(review_schema, rules)
    blocked = blocking_decisions(review_schema)
    if blocked:
        for item in blocked:
            errors.append(
                f"BLOCK: {item['table_name']}.{item.get('column_name', '')} "
                f"{item.get('issue_type', '')} - {item.get('reason', '')}"
            )
    if errors:
        raise ValueError("\n".join(errors))

    table_cache: dict[str, dict[str, Any]] = {}
    for source_table, table_schema in review_schema.items():
        transformed, expressions, derived = transformed_select_sql(
            source_table,
            table_schema,
            rules,
        )
        table_cache[source_table] = {
            "transformed": transformed,
            "expressions": expressions,
            "derived": derived,
        }

    chunks: list[str] = [
        "/* ==================================================================",
        "   SEPRO SILVER LAYER - GENERATED SQL",
        "   ------------------------------------------------------------------",
        "   Generated by: build_silver.py",
        "   Python database access: NONE",
        "   Execution: MANUAL in SQL Server Management Studio (SSMS)",
        "",
        "   SAFETY:",
        "   - This script never silently drops existing Silver tables.",
        "   - If a target table already exists, execution stops and rolls back.",
        "   - Review this file before execution.",
        "   ================================================================== */",
        "",
        relationship_summary_comments(review_schema),
        "",
        "SET NOCOUNT ON;",
        "SET XACT_ABORT ON;",
        "",
        "BEGIN TRY",
        "    BEGIN TRANSACTION;",
        "",
        f"    IF SCHEMA_ID({sql_nvarchar(SILVER_SCHEMA)}) IS NULL",
        f"        EXEC(N'CREATE SCHEMA {q(SILVER_SCHEMA)}');",
    ]

    # Build every table first. PK/FK constraints are added afterward so parent / child
    # creation order does not matter.
    for source_table, table_schema in review_schema.items():
        silver_name = get_silver_table_name(source_table, table_schema)
        cache = table_cache[source_table]
        chunks.extend(
            [
                "",
                "    /* ------------------------------------------------------------",
                f"       TABLE: {BRONZE_SCHEMA}.{source_table} -> {SILVER_SCHEMA}.{silver_name}",
                "       ------------------------------------------------------------ */",
                indent_sql(analyst_decision_comments(source_table, table_schema), 4),
                "",
                indent_sql(target_exists_guard_sql(source_table, table_schema), 4),
                "",
            ]
        )

        for check in schema_runtime_guards_sql(
            source_table,
            table_schema,
            cache["transformed"],
            cache["expressions"],
            cache["derived"],
        ):
            chunks.append(indent_sql(check, 4))
            chunks.append("")

        chunks.append(
            indent_sql(
                create_table_sql(source_table, table_schema, cache["derived"]),
                4,
            )
        )
        chunks.append("")
        chunks.append(
            indent_sql(
                insert_sql(
                    source_table,
                    table_schema,
                    cache["transformed"],
                    cache["derived"],
                ),
                4,
            )
        )

    chunks.extend(
        [
            "",
            "    /* ------------------------------------------------------------",
            "       PRIMARY KEYS",
            "       ------------------------------------------------------------ */",
        ]
    )
    for source_table, table_schema in review_schema.items():
        statement = primary_key_sql(source_table, table_schema)
        if statement:
            chunks.append(indent_sql(statement, 4))

    chunks.extend(
        [
            "",
            "    /* ------------------------------------------------------------",
            "       FOREIGN KEY RELATIONSHIPS",
            "       ------------------------------------------------------------ */",
        ]
    )
    for record in foreign_key_records(review_schema):
        chunks.append(indent_sql(foreign_key_sql(record), 4))

    chunks.extend(
        [
            "",
            "    COMMIT TRANSACTION;",
            "END TRY",
            "BEGIN CATCH",
            "    IF @@TRANCOUNT > 0 ROLLBACK TRANSACTION;",
            "    THROW;",
            "END CATCH;",
            "GO",
            "",
            final_validation_queries(review_schema),
            "",
        ]
    )
    return "\n".join(chunks)


def main() -> None:
    """Generate output/build_silver.sql; never connect to or modify a database."""

    print("=" * 72)
    print("SEPRO SILVER - SQL GENERATOR")
    print("=" * 72)
    print("Database connection: DISABLED")
    print(f"Reviewed contract: {REVIEW_PATH}")

    review_schema = load_json(REVIEW_PATH)
    rules = load_json(RULES_PATH, required=False)
    rules.setdefault("_global", {})
    rules.setdefault("tables", {})

    try:
        sql = generate_sql(review_schema, rules)
    except ValueError as error:
        print("\nSQL generation stopped. Resolve the following review/config issue(s):")
        for line in str(error).splitlines():
            print(f"- {line}")
        raise SystemExit(1)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    SQL_OUTPUT_PATH.write_text(sql, encoding="utf-8")

    relationship_count = len(foreign_key_records(review_schema))
    print("\nSQL generated successfully.")
    print(f"Output: {SQL_OUTPUT_PATH}")
    print(f"Tables: {len(review_schema)}")
    print(f"Proposed/reviewed relationships: {relationship_count}")
    print("\nNext step: open the SQL file, review it, then execute it manually in SSMS.")


if __name__ == "__main__":
    main()
