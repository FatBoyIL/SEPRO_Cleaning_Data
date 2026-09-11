import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from data_quality import (
    has_missing,
    infer_silver_datatype,
    normalize_column_name,
    suggest_primary_key,
)


# =========================================================
# FK NAME ALIASES
# =========================================================
#
# These are only name hints for FK proposal.
# A relationship is still proposed only when actual values match.
#
# Business-specific aliases can be added later if the real columns
# use different names.

FK_NAME_ALIASES = {
    "salesperson_id": "employee_id",
    "sales_owner_id": "employee_id",
    "assigned_salesperson_id": "employee_id",
    "buyer_id": "employee_id",
    "owner_id": "employee_id",
}


# =========================================================
# 1. COLUMN PROPOSAL
# =========================================================

def build_column_proposal(
    df: pd.DataFrame,
) -> Dict:
    """
    JSON format for columns:

    "source_column_name": {
        "name": "...",
        "datatype": "...",
        "nullable": true/false
    }
    """
    columns = {}

    for source_column in df.columns:
        datatype_result = (
            infer_silver_datatype(
                df[source_column],
                source_column,
            )
        )

        columns[source_column] = {
            "name": normalize_column_name(
                source_column
            ),
            "datatype": (
                datatype_result[
                    "suggested_silver_type"
                ]
            ),
            "nullable": has_missing(
                df[source_column]
            ),
        }

    return columns


# =========================================================
# 2. PK PROPOSAL
# =========================================================

def build_primary_key_proposal(
    df: pd.DataFrame,
) -> List[str]:
    """
    Return proposed PK columns.
    Can return:
        ["customer_id"]
        ["snapshot_date", "product_id", "warehouse_id"]
        []
    """
    return suggest_primary_key(df)


# =========================================================
# 3. KEY VALUE NORMALIZATION
# =========================================================

def _key_values(
    series: pd.Series,
) -> pd.Series:
    """
    Normalize only for FK comparison.
    Source data is not modified.
    """
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
    )


# =========================================================
# 4. FK NAME COMPATIBILITY
# =========================================================

def _expected_parent_key_name(
    source_column: str,
) -> str:
    clean_name = normalize_column_name(
        source_column
    )

    return FK_NAME_ALIASES.get(
        clean_name,
        clean_name,
    )


def _is_fk_candidate_column(
    column_name: str,
) -> bool:
    clean_name = normalize_column_name(
        column_name
    )

    return (
        clean_name.endswith("_id")
        or clean_name in FK_NAME_ALIASES
    )


# =========================================================
# 5. FK MATCH RATE
# =========================================================

def calculate_fk_match_rate(
    child_series: pd.Series,
    parent_series: pd.Series,
) -> Tuple[float, int, int]:
    """
    Returns:
        match_rate
        matched_count
        non_missing_child_count
    """
    child = _key_values(
        child_series
    ).dropna()

    child = child[
        child.ne("")
    ]

    parent = set(
        _key_values(
            parent_series
        )
        .dropna()
        .loc[
            lambda s: s.ne("")
        ]
        .unique()
    )

    if len(child) == 0:
        return 0.0, 0, 0

    matched_mask = child.isin(
        parent
    )

    matched_count = int(
        matched_mask.sum()
    )

    total_count = int(
        len(child)
    )

    match_rate = (
        matched_count
        / total_count
    )

    return (
        match_rate,
        matched_count,
        total_count,
    )


# =========================================================
# 6. FK PROPOSAL
# =========================================================

def propose_foreign_keys(
    table_name: str,
    df: pd.DataFrame,
    all_tables: Dict[str, pd.DataFrame],
    primary_keys: Dict[str, List[str]],
    min_match_rate: float = 0.80,
) -> Tuple[List[Dict], List[Dict]]:
    """
    Propose only simple single-column FK relationships.

    JSON output keeps only:
        column
        references

    CSV report keeps extra diagnostics:
        match rate
        matched count
        child non-missing count
    """
    json_foreign_keys = []
    report_rows = []

    for child_column in df.columns:
        if not _is_fk_candidate_column(
            child_column
        ):
            continue

        expected_parent_name = (
            _expected_parent_key_name(
                child_column
            )
        )

        candidates = []

        for parent_table, parent_df in (
            all_tables.items()
        ):
            if parent_table == table_name:
                continue

            parent_pk = primary_keys.get(
                parent_table,
                [],
            )

            # Keep Phase 1 simple:
            # FK proposal only against single-column parent PK.
            if len(parent_pk) != 1:
                continue

            parent_column = parent_pk[0]

            if normalize_column_name(
                parent_column
            ) != expected_parent_name:
                continue

            (
                match_rate,
                matched_count,
                child_non_missing_count,
            ) = calculate_fk_match_rate(
                df[child_column],
                parent_df[parent_column],
            )

            if match_rate < min_match_rate:
                continue

            candidates.append(
                {
                    "parent_table": (
                        parent_table
                    ),
                    "parent_column": (
                        parent_column
                    ),
                    "match_rate": (
                        match_rate
                    ),
                    "matched_count": (
                        matched_count
                    ),
                    "child_non_missing_count": (
                        child_non_missing_count
                    ),
                }
            )

        if not candidates:
            continue

        candidates.sort(
            key=lambda item: (
                item["match_rate"],
                item["matched_count"],
            ),
            reverse=True,
        )

        best = candidates[0]

        json_foreign_keys.append(
            {
                "column": child_column,
                "references": (
                    f"{best['parent_table']}."
                    f"{best['parent_column']}"
                ),
            }
        )

        report_rows.append(
            {
                "table_name": table_name,
                "column_name": child_column,
                "references_table": (
                    best["parent_table"]
                ),
                "references_column": (
                    best["parent_column"]
                ),
                "match_rate_pct": round(
                    best["match_rate"] * 100,
                    2,
                ),
                "matched_count": (
                    best["matched_count"]
                ),
                "child_non_missing_count": (
                    best[
                        "child_non_missing_count"
                    ]
                ),
                "proposal": (
                    "FK candidate based on "
                    "column meaning + actual "
                    "value match."
                ),
            }
        )

    return (
        json_foreign_keys,
        report_rows,
    )


# =========================================================
# 7. TABLE PROPOSAL TEXT
# =========================================================

def build_table_proposal_text(
    primary_key: List[str],
    foreign_keys: List[Dict],
) -> str:
    if primary_key:
        pk_text = (
            " + ".join(primary_key)
        )
        pk_note = (
            f"Proposed PK: {pk_text}."
        )
    else:
        pk_note = (
            "No safe PK found automatically; "
            "review table grain/composite key."
        )

    fk_note = (
        f" Proposed FK count: "
        f"{len(foreign_keys)}."
    )

    return (
        pk_note
        + fk_note
        + " Review this JSON before cleaning."
    )


# =========================================================
# 8. BUILD ONE TABLE
# =========================================================

def build_one_table_proposal(
    table_name: str,
    df: pd.DataFrame,
    all_tables: Dict[str, pd.DataFrame],
    primary_keys: Dict[str, List[str]],
) -> Tuple[Dict, List[Dict]]:
    primary_key = primary_keys[
        table_name
    ]

    (
        foreign_keys,
        fk_report_rows,
    ) = propose_foreign_keys(
        table_name=table_name,
        df=df,
        all_tables=all_tables,
        primary_keys=primary_keys,
    )

    proposal = {
        "table": {
            "name": table_name,
            "primary_key": (
                primary_key
            ),
            "foreign_keys": (
                foreign_keys
            ),
            "proposal": (
                build_table_proposal_text(
                    primary_key,
                    foreign_keys,
                )
            ),
        },
        "columns": (
            build_column_proposal(df)
        ),
    }

    return (
        proposal,
        fk_report_rows,
    )


# =========================================================
# 9. BUILD ALL TABLES
# =========================================================

def build_schema_proposal(
    all_tables: Dict[str, pd.DataFrame],
) -> Tuple[Dict, pd.DataFrame, pd.DataFrame]:
    """
    Return:
        master_json
        column_report
        key_report
    """
    primary_keys = {
        table_name: (
            build_primary_key_proposal(
                df
            )
        )
        for table_name, df in (
            all_tables.items()
        )
    }

    master_json = {}
    column_rows = []
    key_rows = []

    for table_name, df in (
        all_tables.items()
    ):
        (
            table_proposal,
            fk_report_rows,
        ) = build_one_table_proposal(
            table_name=table_name,
            df=df,
            all_tables=all_tables,
            primary_keys=primary_keys,
        )

        master_json[
            table_name
        ] = table_proposal

        # Column CSV
        for (
            source_column,
            column_config,
        ) in table_proposal[
            "columns"
        ].items():
            column_rows.append(
                {
                    "table_name": (
                        table_name
                    ),
                    "source_column": (
                        source_column
                    ),
                    "proposed_name": (
                        column_config[
                            "name"
                        ]
                    ),
                    "datatype": (
                        column_config[
                            "datatype"
                        ]
                    ),
                    "nullable": (
                        column_config[
                            "nullable"
                        ]
                    ),
                }
            )

        # PK rows
        primary_key = table_proposal[
            "table"
        ]["primary_key"]

        if primary_key:
            for position, column in (
                enumerate(
                    primary_key,
                    start=1,
                )
            ):
                key_rows.append(
                    {
                        "table_name": (
                            table_name
                        ),
                        "key_type": (
                            "PRIMARY_KEY"
                        ),
                        "column_name": (
                            column
                        ),
                        "key_position": (
                            position
                        ),
                        "references_table": "",
                        "references_column": "",
                        "match_rate_pct": "",
                        "proposal": (
                            "Proposed from "
                            "non-missing + "
                            "uniqueness/grain "
                            "heuristics."
                        ),
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
                    "proposal": (
                        "No safe automatic PK. "
                        "Review grain/composite key."
                    ),
                }
            )

        # FK rows
        for row in fk_report_rows:
            key_rows.append(
                {
                    "table_name": (
                        row["table_name"]
                    ),
                    "key_type": (
                        "FOREIGN_KEY"
                    ),
                    "column_name": (
                        row["column_name"]
                    ),
                    "key_position": "",
                    "references_table": (
                        row[
                            "references_table"
                        ]
                    ),
                    "references_column": (
                        row[
                            "references_column"
                        ]
                    ),
                    "match_rate_pct": (
                        row[
                            "match_rate_pct"
                        ]
                    ),
                    "proposal": (
                        row["proposal"]
                    ),
                }
            )

    column_report = pd.DataFrame(
        column_rows,
        columns=[
            "table_name",
            "source_column",
            "proposed_name",
            "datatype",
            "nullable",
        ],
    )

    key_report = pd.DataFrame(
        key_rows,
        columns=[
            "table_name",
            "key_type",
            "column_name",
            "key_position",
            "references_table",
            "references_column",
            "match_rate_pct",
            "proposal",
        ],
    )

    return (
        master_json,
        column_report,
        key_report,
    )


# =========================================================
# 10. EXPORT
# =========================================================

def export_schema_proposal(
    master_json: Dict,
    column_report: pd.DataFrame,
    key_report: pd.DataFrame,
    report_dir: Path,
    review_dir: Path,
) -> Dict[str, Path]:

    proposal_dir = (
        report_dir
        / "03_schema_proposal"
    )

    proposal_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    review_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    column_path = (
        proposal_dir
        / "silver_column_proposals.csv"
    )

    key_path = (
        proposal_dir
        / "silver_key_proposals.csv"
    )

    json_path = (
        review_dir
        / "silver_schema_review.json"
    )

    column_report.to_csv(
        column_path,
        index=False,
        encoding="utf-8-sig",
    )

    key_report.to_csv(
        key_path,
        index=False,
        encoding="utf-8-sig",
    )

    with open(
        json_path,
        "w",
        encoding="utf-8",
    ) as file:
        json.dump(
            master_json,
            file,
            ensure_ascii=False,
            indent=2,
        )

    return {
        "column_report": (
            column_path
        ),
        "key_report": (
            key_path
        ),
        "review_json": (
            json_path
        ),
    }
