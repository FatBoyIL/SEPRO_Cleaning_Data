"""Standardize fake NULL markers in Landing/Bronze data."""

from __future__ import annotations

from typing import Iterable, Tuple

import pandas as pd



def standardize_null_markers(
    df: pd.DataFrame,
    null_markers: Iterable[str],
    include_columns: Iterable[str] | None = None,
) -> Tuple[pd.DataFrame, pd.Series]:
    """Convert configured fake-NULL markers only in analyst-approved columns.

    ``include_columns=None`` preserves the original all-column behavior for
    backwards compatibility. Passing an iterable limits transformation to those
    columns, which is how the final decision-driven build operates. Existing
    real NULL values are never imputed.
    """

    result = df.copy()
    changed_rows = pd.Series(False, index=result.index, dtype=bool)
    normalized_markers = {str(value).strip().lower() for value in null_markers}
    allowed = None if include_columns is None else set(include_columns)

    for column in result.columns:
        if allowed is not None and column not in allowed:
            continue
        if not (
            pd.api.types.is_object_dtype(result[column].dtype)
            or pd.api.types.is_string_dtype(result[column].dtype)
        ):
            continue
        text = result[column].astype("string")
        normalized = text.str.strip().str.lower()
        mask = result[column].notna() & normalized.isin(normalized_markers)
        if mask.any():
            result.loc[mask, column] = pd.NA
            changed_rows |= mask

    return result, changed_rows
