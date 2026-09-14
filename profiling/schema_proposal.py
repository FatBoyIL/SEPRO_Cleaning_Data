"""Build analyst-reviewable Silver schema proposals from Bronze DataFrames."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from profiling.data_quality import (
    has_missing,
    infer_silver_datatype,
    normalize_column_name,
    suggest_primary_key,
)


# =========================================================
# BASIC HELPERS
# =========================================================


def normalize_table_name(table_name: str) -> str:
    """Return the default Silver table name by removing a trailing ``_raw`` suffix."""

    return table_name[:-4] if table_name.endswith("_raw") else table_name


def _normalize_key_values(series: pd.Series) -> pd.Series:
    """Normalize key values for PK/FK matching without changing source data."""

    return (
        series.astype("string")
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .str.lower()
    )


def calculate_fk_match_rate(child: pd.Series, parent: pd.Series) -> Tuple[float, int, int]:
    """Calculate how many non-missing child values exist in a candidate parent key."""

    child_values = _normalize_key_values(child.dropna())
    parent_values = set(_normalize_key_values(parent.dropna()).tolist())
    child_values = child_values[child_values.ne("")]
    child_count = int(len(child_values))
    if child_count == 0:
        return 0.0, 0, 0
    matched = int(child_values.isin(parent_values).sum())
    return matched / child_count, matched, child_count


# =========================================================
# COLUMN / KEY PROPOSALS
# =========================================================


def build_column_proposal(df: pd.DataFrame) -> Dict:
    """Build normalized name, proposed datatype, and observed nullable metadata.

    ``nullable`` reflects whether missing data is observed in Bronze. The analyst
    should edit it when the business contract differs from current observations.
    """

    columns: Dict = {}
    for source_column in df.columns:
        datatype_result = infer_silver_datatype(df[source_column], source_column)
        columns[source_column] = {
            "name": normalize_column_name(source_column),
            "datatype": datatype_result["suggested_silver_type"],
            "nullable": has_missing(df[source_column]),
        }
    return columns


def build_primary_key_proposal(df: pd.DataFrame) -> List[str]:
    """Return an automatically inferred PK candidate for manual analyst review."""

    return suggest_primary_key(df)


def propose_foreign_keys(
    table_name: str,
    df: pd.DataFrame,
    all_tables: Dict[str, pd.DataFrame],
    primary_keys: Dict[str, List[str]],
    min_match_rate: float = 0.80,
) -> Tuple[List[Dict], List[Dict]]:
    """Propose FKs using key-name meaning plus actual value overlap.

    Only single-column parent PKs are considered automatically. Composite parent
    relationships remain a manual review task because grain semantics matter.
    """

    json_foreign_keys: List[Dict] = []
    report_rows: List[Dict] = []

    for child_column in df.columns:
        normalized_child = normalize_column_name(child_column)
        if not normalized_child.endswith("_id"):
            continue

        candidates = []
        for parent_table, parent_df in all_tables.items():
            if parent_table == table_name:
                continue
            parent_pk = primary_keys.get(parent_table, [])
            if len(parent_pk) != 1:
                continue
            parent_column = parent_pk[0]
            if normalize_column_name(parent_column) != normalized_child:
                continue

            match_rate, matched_count, child_count = calculate_fk_match_rate(
                df[child_column], parent_df[parent_column]
            )
            if match_rate < min_match_rate:
                continue
            candidates.append(
                {
                    "parent_table": parent_table,
                    "parent_column": parent_column,
                    "match_rate": match_rate,
                    "matched_count": matched_count,
                    "child_count": child_count,
                }
            )

        if not candidates:
            continue

        candidates.sort(
            key=lambda item: (item["match_rate"], item["matched_count"]), reverse=True
        )
        best = candidates[0]
        json_foreign_keys.append(
            {
                "column": child_column,
                "references": f"{best['parent_table']}.{best['parent_column']}",
            }
        )
        report_rows.append(
            {
                "table_name": table_name,
                "column_name": child_column,
                "references_table": best["parent_table"],
                "references_column": best["parent_column"],
                "match_rate_pct": round(best["match_rate"] * 100, 2),
                "proposal": "FK candidate based on matching key name and observed values.",
            }
        )

    return json_foreign_keys, report_rows


def build_table_proposal_text(primary_key: List[str], foreign_keys: List[Dict]) -> str:
    """Create a short human-readable note summarizing the inferred keys."""

    if primary_key:
        pk_note = f"Proposed PK: {' + '.join(primary_key)}."
    else:
        pk_note = "No safe PK found automatically; review table grain/composite key."
    return f"{pk_note} Proposed FK count: {len(foreign_keys)}. Review this JSON before cleaning."


def build_one_table_proposal(
    table_name: str,
    df: pd.DataFrame,
    all_tables: Dict[str, pd.DataFrame],
    primary_keys: Dict[str, List[str]],
) -> Tuple[Dict, List[Dict]]:
    """Combine column, PK, and FK proposals into one editable table schema object."""

    primary_key = primary_keys[table_name]
    foreign_keys, fk_report_rows = propose_foreign_keys(
        table_name=table_name,
        df=df,
        all_tables=all_tables,
        primary_keys=primary_keys,
    )
    proposal = {
        "table": {
            "name": table_name,
            "silver_name": normalize_table_name(table_name),
            "primary_key": primary_key,
            "foreign_keys": foreign_keys,
            "proposal": build_table_proposal_text(primary_key, foreign_keys),
        },
        "columns": build_column_proposal(df),
    }
    return proposal, fk_report_rows


# =========================================================
# BUILD / FLATTEN REVIEW OUTPUTS
# =========================================================


def build_schema_proposal(
    all_tables: Dict[str, pd.DataFrame],
) -> Tuple[Dict, pd.DataFrame, pd.DataFrame]:
    """Build the master JSON plus flattened column/key proposal DataFrames."""

    primary_keys = {
        table_name: build_primary_key_proposal(df)
        for table_name, df in all_tables.items()
    }

    master_json: Dict = {}
    column_rows: List[Dict] = []
    key_rows: List[Dict] = []

    for table_name, df in all_tables.items():
        table_proposal, fk_report_rows = build_one_table_proposal(
            table_name=table_name,
            df=df,
            all_tables=all_tables,
            primary_keys=primary_keys,
        )
        master_json[table_name] = table_proposal

        for source_column, config in table_proposal["columns"].items():
            column_rows.append(
                {
                    "table_name": table_name,
                    "source_column": source_column,
                    "proposed_name": config["name"],
                    "datatype": config["datatype"],
                    "nullable": config["nullable"],
                }
            )

        pk = table_proposal["table"]["primary_key"]
        if pk:
            for position, column in enumerate(pk, start=1):
                key_rows.append(
                    {
                        "table_name": table_name,
                        "key_type": "PRIMARY_KEY",
                        "column_name": column,
                        "key_position": position,
                        "references_table": "",
                        "references_column": "",
                        "match_rate_pct": "",
                        "proposal": "Proposed from uniqueness/grain heuristics.",
                    }
                )
        else:
            key_rows.append(
                {
                    "table_name": table_name,
                    "key_type": "PRIMARY_KEY",
                    "column_name": "",
                    "key_position": "",
                    "references_table": "",
                    "references_column": "",
                    "match_rate_pct": "",
                    "proposal": "No safe automatic PK; review grain/composite key.",
                }
            )

        for row in fk_report_rows:
            key_rows.append(
                {
                    "table_name": row["table_name"],
                    "key_type": "FOREIGN_KEY",
                    "column_name": row["column_name"],
                    "key_position": "",
                    "references_table": row["references_table"],
                    "references_column": row["references_column"],
                    "match_rate_pct": row["match_rate_pct"],
                    "proposal": row["proposal"],
                }
            )

    return (
        master_json,
        pd.DataFrame(column_rows),
        pd.DataFrame(key_rows),
    )


def build_review_dataframe(master_json: Dict, table_profiles: pd.DataFrame) -> pd.DataFrame:
    """Flatten the machine proposal into the analyst-editable schema Review CSV.

    ``source_column`` is preserved explicitly so the CSV can later be compiled
    back into a machine-readable contract even when the analyst renames the
    Silver column. Table-level context intentionally repeats on every row.
    """

    profile_lookup = {}
    if not table_profiles.empty:
        profile_lookup = table_profiles.set_index("table_name").to_dict(orient="index")

    rows: List[Dict] = []
    for table_name, table_info in master_json.items():
        table_meta = table_info["table"]
        pk = list(table_meta.get("primary_key", []))
        pk_order = {column: position for position, column in enumerate(pk, start=1)}
        fk_lookup = {
            item["column"]: item["references"]
            for item in table_meta.get("foreign_keys", [])
        }
        profile = profile_lookup.get(table_name, {})

        for source_column, config in table_info["columns"].items():
            rows.append(
                {
                    "table_name": table_name,
                    "silver_table_name": table_meta.get("silver_name", normalize_table_name(table_name)),
                    "allow_no_primary_key": False,
                    "source_column": source_column,
                    "column_name": config["name"],
                    "datatype": config["datatype"],
                    "nullable": config["nullable"],
                    "is_pk": source_column in pk or config["name"] in pk,
                    "pk_order": pk_order.get(source_column, ""),
                    "is_fk": source_column in fk_lookup or config["name"] in fk_lookup,
                    "fk_reference": fk_lookup.get(source_column, fk_lookup.get(config["name"], "")),
                    "row_count": int(profile.get("row_count", 0)),
                    "duplicate_row_count": int(profile.get("exact_duplicate_rows", 0)),
                }
            )

    return pd.DataFrame(
        rows,
        columns=[
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
            "row_count",
            "duplicate_row_count",
        ],
    )


def merge_schema_review(existing_df: pd.DataFrame, proposal_df: pd.DataFrame) -> pd.DataFrame:
    """Refresh machine context while preserving prior analyst schema decisions.

    Rows are matched by ``table_name + source_column``. New Bronze columns are
    added automatically; removed Bronze columns disappear from the refreshed
    review. Analyst-editable fields are preserved for matching rows.
    """

    if existing_df is None or existing_df.empty:
        return proposal_df.copy()

    key_columns = ["table_name", "source_column"]
    if not set(key_columns).issubset(existing_df.columns):
        return proposal_df.copy()

    editable = [
        "silver_table_name",
        "allow_no_primary_key",
        "column_name",
        "datatype",
        "nullable",
        "is_pk",
        "pk_order",
        "is_fk",
        "fk_reference",
    ]

    previous = existing_df[key_columns + [c for c in editable if c in existing_df.columns]].copy()
    merged = proposal_df.merge(previous, on=key_columns, how="left", suffixes=("", "__old"))

    for column in editable:
        old_column = f"{column}__old"
        if old_column not in merged.columns:
            continue
        keep_old = merged[old_column].notna() & merged[old_column].astype("string").ne("")
        merged.loc[keep_old, column] = merged.loc[keep_old, old_column]
        merged = merged.drop(columns=[old_column])

    return merged[proposal_df.columns]


def export_review_files(
    master_json: Dict,
    proposal_df: pd.DataFrame,
    review_dir: Path,
) -> Dict[str, Path]:
    """Write machine proposal files and refresh the analyst-editable Review CSV.

    The proposal files are regenerated on every Review run. The editable schema
    CSV preserves prior analyst decisions when possible. No final build contract
    JSON is created here; ``review_to_json.py`` owns that responsibility.
    """

    review_dir.mkdir(parents=True, exist_ok=True)
    proposal_json_path = review_dir / "silver_schema_proposal.json"
    proposal_csv_path = review_dir / "silver_schema_proposal.csv"
    review_csv_path = review_dir / "silver_schema_review.csv"

    with proposal_json_path.open("w", encoding="utf-8") as file:
        json.dump(master_json, file, ensure_ascii=False, indent=2)
    proposal_df.to_csv(proposal_csv_path, index=False, encoding="utf-8-sig")

    existing_df = None
    if review_csv_path.exists():
        try:
            existing_df = pd.read_csv(review_csv_path)
        except Exception:
            existing_df = None

    review_df = merge_schema_review(existing_df, proposal_df)
    review_df.to_csv(review_csv_path, index=False, encoding="utf-8-sig")

    return {
        "proposal_json": proposal_json_path,
        "proposal_csv": proposal_csv_path,
        "review_csv": review_csv_path,
    }
