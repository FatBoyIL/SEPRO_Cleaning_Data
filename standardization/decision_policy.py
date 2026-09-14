"""Helpers for applying analyst Data Quality decisions during Silver build."""

from __future__ import annotations

from typing import Dict, Iterable, List


# =========================================================
# DECISION ACCESS
# =========================================================


def quality_decisions(table_schema: Dict) -> List[Dict]:
    """Return normalized quality-decision rows stored in one table contract."""

    decisions = table_schema.get("quality_decisions", [])
    return decisions if isinstance(decisions, list) else []


def blocking_decisions(review_schema: Dict) -> List[Dict]:
    """Return every analyst decision marked BLOCK across the final contract."""

    rows: List[Dict] = []
    for table_name, table_schema in review_schema.items():
        for decision in quality_decisions(table_schema):
            if str(decision.get("decision", "")).upper() != "BLOCK":
                continue
            row = dict(decision)
            row["table_name"] = table_name
            rows.append(row)
    return rows


def _matches_column(decision_column: str, candidates: Iterable[str]) -> bool:
    """Match a decision column against source/Silver aliases, including table-level blanks."""

    decision_column = str(decision_column or "").strip()
    candidate_set = {str(value or "").strip() for value in candidates}
    return decision_column in candidate_set


def find_decisions(
    table_schema: Dict,
    issue_type: str,
    column_candidates: Iterable[str] = ("",),
) -> List[Dict]:
    """Find analyst decisions for one issue type and source/Silver column alias set."""

    target_issue = str(issue_type).upper()
    matches = []
    for decision in quality_decisions(table_schema):
        if str(decision.get("issue_type", "")).upper() != target_issue:
            continue
        if _matches_column(decision.get("column_name", ""), column_candidates):
            matches.append(decision)
    return matches


def approved_fix_columns(
    table_schema: Dict,
    issue_type: str,
    action: str,
) -> List[str]:
    """Return decision column identifiers approved for a specific automatic fix."""

    issue_type = str(issue_type).upper()
    action = str(action).upper()
    columns = []
    for decision in quality_decisions(table_schema):
        if str(decision.get("issue_type", "")).upper() != issue_type:
            continue
        if str(decision.get("decision", "")).upper() != "FIX":
            continue
        if str(decision.get("action", "")).upper() != action:
            continue
        column = str(decision.get("column_name", "")).strip()
        if column:
            columns.append(column)
    return columns


def table_action_is_fixed(
    table_schema: Dict,
    issue_type: str,
    action: str,
) -> bool:
    """Return True when a table-level issue has an approved FIX/action pair."""

    for decision in find_decisions(table_schema, issue_type, column_candidates=("",)):
        if (
            str(decision.get("decision", "")).upper() == "FIX"
            and str(decision.get("action", "")).upper() == str(action).upper()
        ):
            return True
    return False


def table_issue_is_kept(table_schema: Dict, issue_type: str) -> bool:
    """Return True when the analyst explicitly accepts a table-level issue as KEEP."""

    for decision in find_decisions(table_schema, issue_type, column_candidates=("",)):
        if str(decision.get("decision", "")).upper() == "KEEP":
            return True
    return False
