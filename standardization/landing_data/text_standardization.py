"""Normalize safe text formatting without changing business meaning."""

from __future__ import annotations

from typing import Iterable, Tuple

import pandas as pd



def standardize_text_columns(
    df: pd.DataFrame,
    skip_columns: Iterable[str] | None = None,
    include_columns: Iterable[str] | None = None,
) -> Tuple[pd.DataFrame, pd.Series]:
    """Trim/collapse whitespace only in analyst-approved text columns.

    ``include_columns=None`` keeps the historical all-text-column behavior. The
    decision-driven build passes a concrete allow-list. Case is deliberately
    preserved.
    """

    result = df.copy()
    changed_rows = pd.Series(False, index=result.index, dtype=bool)
    skip = set(skip_columns or [])
    allowed = None if include_columns is None else set(include_columns)

    for column in result.columns:
        if column in skip:
            continue
        if allowed is not None and column not in allowed:
            continue
        if not (
            pd.api.types.is_object_dtype(result[column].dtype)
            or pd.api.types.is_string_dtype(result[column].dtype)
        ):
            continue

        original = result[column].astype("string")
        cleaned = original.str.strip().str.replace(r"\s+", " ", regex=True)
        mask = original.notna() & cleaned.ne(original).fillna(False)
        result[column] = cleaned
        changed_rows |= mask

    return result, changed_rows
