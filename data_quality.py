import re
from typing import Dict, List, Tuple

import pandas as pd


# =========================================================
# MISSING VALUE SETTINGS
# =========================================================

NULL_MARKERS = {
    "",
    "na",
    "n/a",
    "n.a.",
    "null",
    "none",
    "unknown",
    "-",
    "--",
    "not available",
}


# =========================================================
# SMALL HELPERS
# =========================================================

def normalize_column_name(column_name: str) -> str:
    """
    Create a consistent Silver-style name from an inconsistent Bronze name.
    This proposes naming only; it does not rename the source DataFrame.

    The ``_raw`` suffix identifies the Bronze representation, so it is removed
    from the proposed Silver name.

    Example:
        "Order Date Raw" -> "order_date"
        "customer-id"    -> "customer_id"
        "province_raw"   -> "province"
    """
    name = str(column_name).strip().lower()
    name = re.sub(r"[^a-z0-9]+", "_", name)
    name = re.sub(r"_+", "_", name).strip("_")

    if name.endswith("_raw"):
        name = name[:-4]

    return name


def _normalized_string_series(series: pd.Series) -> pd.Series:
    """
    Create a temporary normalized text view for comparisons such as missing
    markers and text variants without modifying the original source values.
    """
    return (
        series.astype("string")
        .str.strip()
        .str.replace(r"\s+", " ", regex=True)
        .str.lower()
    )


def get_missing_masks(series: pd.Series) -> Tuple[pd.Series, pd.Series]:
    """
    Separate real NULL values from configured text placeholders because they
    represent different Data Quality problems and should be reported apart.
    """
    real_null_mask = series.isna()

    normalized = _normalized_string_series(series)
    marker_null_mask = (
        ~real_null_mask
        & normalized.isin(NULL_MARKERS)
    )

    return real_null_mask, marker_null_mask


def has_missing(series: pd.Series) -> bool:
    """
    Provide one shared nullable check so other modules use the same definition
    of a missing value, including schema proposal logic.
    """
    real_null_mask, marker_null_mask = get_missing_masks(series)
    return bool((real_null_mask | marker_null_mask).any())


def _effective_non_missing(series: pd.Series) -> pd.Series:
    """
    Exclude real NULLs and configured text markers before uniqueness and type
    inference so placeholders do not distort profiling evidence.
    """
    real_null_mask, marker_null_mask = get_missing_masks(series)
    return series[~(real_null_mask | marker_null_mask)]


def _numeric_ratio(values: pd.Series) -> float:
    """Measure how much of a column parses as numeric without converting it."""
    if len(values) == 0:
        return 0.0

    text_values = (
        values.astype("string")
        .str.strip()
        .str.replace(",", "", regex=False)
    )

    parsed = pd.to_numeric(text_values, errors="coerce")
    return float(parsed.notna().mean())


def _date_ratio(values: pd.Series) -> float:
    """Measure parseable date values while filtering out plain numeric IDs."""
    if len(values) == 0:
        return 0.0

    text_values = values.astype("string").str.strip()

    # Avoid interpreting plain IDs/numbers as dates.
    date_like_mask = text_values.str.contains(
        r"[-/:T ]",
        regex=True,
        na=False,
    )

    if not date_like_mask.any():
        return 0.0

    candidate_values = text_values[date_like_mask]

    parsed = pd.to_datetime(
        candidate_values,
        errors="coerce",
    )

    return float(parsed.notna().sum() / len(text_values))


def infer_silver_datatype(
    series: pd.Series,
    column_name: str,
) -> Dict:
    """
    Propose a Silver datatype from the column name and observed values; this
    function does not convert the source data.

    Identifier-like fields stay strings to preserve meaningful leading zeros.
    Boolean inference requires both a boolean-like name and values, while date
    inference combines name hints with parse success to reduce false matches.
    Numeric inference is deliberately conservative and requires a very high
    parse ratio.

    Output values:
        string
        integer
        decimal
        date
        datetime
        boolean
    """
    clean_name = normalize_column_name(column_name)
    values = _effective_non_missing(series)

    numeric_ratio = _numeric_ratio(values)
    date_ratio = _date_ratio(values)

    if len(values) == 0:
        return {
            "suggested_silver_type": "string",
            "numeric_ratio": 0.0,
            "date_ratio": 0.0,
        }

    # IDs, codes, SKUs, tax codes, phone numbers, tracking values:
    # keep as string to avoid destroying leading zeros.
    string_name_tokens = (
        "_id",
        "code",
        "sku",
        "tax",
        "phone",
        "mobile",
        "tracking",
        "postal",
        "zip",
    )

    if any(token in clean_name for token in string_name_tokens):
        return {
            "suggested_silver_type": "string",
            "numeric_ratio": round(numeric_ratio, 4),
            "date_ratio": round(date_ratio, 4),
        }

    normalized_values = _normalized_string_series(values)
    unique_values = set(normalized_values.dropna().unique())

    boolean_tokens = {
        "true", "false",
        "yes", "no",
        "y", "n",
        "0", "1",
    }

    boolean_name_hint = (
        clean_name.startswith("is_")
        or clean_name.startswith("has_")
        or clean_name.endswith("_flag")
        or clean_name.endswith("_indicator")
    )

    # Require both name and value evidence before classifying a boolean column.
    if (
        boolean_name_hint
        and unique_values
        and unique_values.issubset(boolean_tokens)
    ):
        return {
            "suggested_silver_type": "boolean",
            "numeric_ratio": round(numeric_ratio, 4),
            "date_ratio": round(date_ratio, 4),
        }

    date_name_hint = any(
        token in clean_name
        for token in (
            "date",
            "datetime",
            "timestamp",
            "_at",
            "time",
        )
    )

    # Column-name hints plus parse success are stronger evidence than values
    # alone, which can make numeric fields look date-like.
    if date_name_hint and date_ratio >= 0.80:
        contains_time = (
            values.astype("string")
            .str.contains(
                r"\d{1,2}:\d{2}",
                regex=True,
                na=False,
            )
            .any()
        )

        return {
            "suggested_silver_type": (
                "datetime"
                if contains_time
                else "date"
            ),
            "numeric_ratio": round(numeric_ratio, 4),
            "date_ratio": round(date_ratio, 4),
        }

    # Keep automatic datatype proposals conservative when source values vary.
    if numeric_ratio >= 0.98:
        numeric_values = pd.to_numeric(
            values.astype("string")
            .str.strip()
            .str.replace(",", "", regex=False),
            errors="coerce",
        ).dropna()

        if len(numeric_values) > 0:
            integer_ratio = float(
                ((numeric_values % 1) == 0).mean()
            )

            if integer_ratio >= 0.98:
                return {
                    "suggested_silver_type": "integer",
                    "numeric_ratio": round(numeric_ratio, 4),
                    "date_ratio": round(date_ratio, 4),
                }

        return {
            "suggested_silver_type": "decimal",
            "numeric_ratio": round(numeric_ratio, 4),
            "date_ratio": round(date_ratio, 4),
        }

    if date_ratio >= 0.95:
        contains_time = (
            values.astype("string")
            .str.contains(
                r"\d{1,2}:\d{2}",
                regex=True,
                na=False,
            )
            .any()
        )

        return {
            "suggested_silver_type": (
                "datetime"
                if contains_time
                else "date"
            ),
            "numeric_ratio": round(numeric_ratio, 4),
            "date_ratio": round(date_ratio, 4),
        }

    return {
        "suggested_silver_type": "string",
        "numeric_ratio": round(numeric_ratio, 4),
        "date_ratio": round(date_ratio, 4),
    }


def _normalized_key_series(series: pd.Series) -> pd.Series:
    """
    Normalize key values only for uniqueness comparison so casing and
    surrounding spaces do not create false uniqueness; source values are kept.
    """
    return (
        series.astype("string")
        .str.strip()
        .str.lower()
    )


def suggest_primary_key(df: pd.DataFrame) -> List[str]:
    """
    Suggest a candidate primary key from current data quality and uniqueness.
    This is a schema proposal, not confirmation of business truth.

    The search first prefers a non-missing unique single column, especially an
    ``*_id`` field, then tests small combinations of likely grain columns.
    """
    if df.empty or len(df.columns) == 0:
        return []

    single_candidates = []

    for position, column in enumerate(df.columns):
        series = df[column]

        if has_missing(series):
            continue

        normalized = _normalized_key_series(series)

        if normalized.nunique(dropna=False) != len(df):
            continue

        clean_name = normalize_column_name(column)

        score = 0

        if clean_name == "id":
            score += 100

        if clean_name.endswith("_id"):
            score += 120

        if "line_id" in clean_name:
            score += 20

        score += max(0, 30 - position)

        single_candidates.append(
            (score, column)
        )

    if single_candidates:
        single_candidates.sort(
            key=lambda item: item[0],
            reverse=True,
        )

        return [single_candidates[0][1]]

    # Composite-key search.
    # Restrict candidate pool to likely grain columns.
    likely_columns = []

    for column in df.columns:
        clean_name = normalize_column_name(column)

        if has_missing(df[column]):
            continue

        if (
            clean_name.endswith("_id")
            or "date" in clean_name
            or "snapshot" in clean_name
            or "line" in clean_name
            or "sequence" in clean_name
        ):
            likely_columns.append(column)

    # Limit the candidate pool to avoid a combinatorial search over every field.
    likely_columns = likely_columns[:8]

    from itertools import combinations

    # More complex grains require manual review rather than automatic guessing.
    for size in (2, 3):
        for combo in combinations(
            likely_columns,
            size,
        ):
            if not df.duplicated(
                subset=list(combo),
                keep=False,
            ).any():
                return list(combo)

    return []


# =========================================================
# 1. TABLE PROFILE
# =========================================================

def profile_table(
    table_name: str,
    df: pd.DataFrame,
) -> Dict:
    """Summarize table size, shape, NULL volume, and exact duplicates."""
    return {
        "table_name": table_name,
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "real_null_cells": int(df.isna().sum().sum()),
        "exact_duplicate_rows": int(
            df.duplicated(keep=False).sum()
        ),
    }


# =========================================================
# 2. COLUMN PROFILE
# =========================================================

def profile_columns(
    table_name: str,
    df: pd.DataFrame,
) -> List[Dict]:
    """Profile raw datatype, missingness, and effective uniqueness per column."""
    rows = []

    for column in df.columns:
        series = df[column]

        real_null_mask, marker_null_mask = (
            get_missing_masks(series)
        )

        missing_mask = (
            real_null_mask
            | marker_null_mask
        )

        # Exclude configured markers so placeholder values do not count as
        # meaningful distinct values in effective uniqueness.
        effective_values = series[~missing_mask]

        row_count = len(series)
        missing_count = int(missing_mask.sum())

        rows.append(
            {
                "table_name": table_name,
                "column_name": column,
                "pandas_dtype": str(series.dtype),
                "row_count": int(row_count),
                "real_null_count": int(
                    real_null_mask.sum()
                ),
                "marker_null_count": int(
                    marker_null_mask.sum()
                ),
                "missing_count": missing_count,
                "missing_pct": round(
                    (
                        missing_count
                        / row_count
                        * 100
                    )
                    if row_count
                    else 0.0,
                    4,
                ),
                "unique_count": int(
                    series.nunique(
                        dropna=False
                    )
                ),
                "effective_unique_count": int(
                    effective_values.nunique(
                        dropna=True
                    )
                ),
            }
        )

    return rows


# =========================================================
# 3. MISSING VALUES
# =========================================================

def check_missing_values(
    table_name: str,
    df: pd.DataFrame,
) -> List[Dict]:
    """Report real NULLs separately from text placeholders with examples."""
    rows = []

    for column in df.columns:
        series = df[column]

        real_null_mask, marker_null_mask = (
            get_missing_masks(series)
        )

        missing_mask = (
            real_null_mask
            | marker_null_mask
        )

        marker_examples = (
            series[marker_null_mask]
            .astype("string")
            .drop_duplicates()
            .head(5)
            .tolist()
        )

        row_count = len(series)
        missing_count = int(missing_mask.sum())

        rows.append(
            {
                "table_name": table_name,
                "column_name": column,
                "row_count": int(row_count),
                "real_null_count": int(
                    real_null_mask.sum()
                ),
                "marker_null_count": int(
                    marker_null_mask.sum()
                ),
                "missing_count": missing_count,
                "missing_pct": round(
                    (
                        missing_count
                        / row_count
                        * 100
                    )
                    if row_count
                    else 0.0,
                    4,
                ),
                "marker_examples": " | ".join(
                    map(str, marker_examples)
                ),
            }
        )

    return rows


# =========================================================
# 4. PRIMARY KEY QUALITY
# =========================================================

def check_primary_key(
    table_name: str,
    df: pd.DataFrame,
) -> Dict:
    """Validate the proposed PK for missing values and raw/normalized duplicates."""
    pk_columns = suggest_primary_key(df)

    if not pk_columns:
        return {
            "table_name": table_name,
            "pk_column": "",
            "row_count": int(len(df)),
            "missing_pk_count": "",
            "duplicate_pk_groups": "",
            "duplicate_pk_rows": "",
            "normalized_duplicate_pk_groups": "",
            "status": "REVIEW_PK",
        }

    pk_label = " + ".join(pk_columns)

    missing_mask = pd.Series(
        False,
        index=df.index,
    )

    for column in pk_columns:
        real_null_mask, marker_null_mask = (
            get_missing_masks(df[column])
        )
        missing_mask = (
            missing_mask
            | real_null_mask
            | marker_null_mask
        )

    duplicate_mask = df.duplicated(
        subset=pk_columns,
        keep=False,
    )

    duplicate_rows = int(
        duplicate_mask.sum()
    )

    if duplicate_rows:
        duplicate_groups = int(
            df.loc[
                duplicate_mask,
                pk_columns,
            ]
            .drop_duplicates()
            .shape[0]
        )
    else:
        duplicate_groups = 0

    normalized_df = pd.DataFrame(
        {
            column: _normalized_key_series(
                df[column]
            )
            for column in pk_columns
        }
    )

    normalized_duplicate_mask = (
        normalized_df.duplicated(
            subset=pk_columns,
            keep=False,
        )
    )

    if normalized_duplicate_mask.any():
        normalized_duplicate_groups = int(
            normalized_df.loc[
                normalized_duplicate_mask,
                pk_columns,
            ]
            .drop_duplicates()
            .shape[0]
        )
    else:
        normalized_duplicate_groups = 0

    status = (
        "OK"
        if (
            int(missing_mask.sum()) == 0
            and duplicate_groups == 0
            and normalized_duplicate_groups == 0
        )
        else "REVIEW_PK"
    )

    return {
        "table_name": table_name,
        "pk_column": pk_label,
        "row_count": int(len(df)),
        "missing_pk_count": int(
            missing_mask.sum()
        ),
        "duplicate_pk_groups": duplicate_groups,
        "duplicate_pk_rows": duplicate_rows,
        "normalized_duplicate_pk_groups": (
            normalized_duplicate_groups
        ),
        "status": status,
    }


# =========================================================
# 5. EXACT DUPLICATES
# =========================================================

def check_exact_duplicates(
    table_name: str,
    df: pd.DataFrame,
) -> List[Dict]:
    """Find fully duplicated rows that can distort counts and key inference."""
    duplicate_mask = df.duplicated(
        keep=False
    )

    affected_rows = int(
        duplicate_mask.sum()
    )

    if affected_rows == 0:
        return []

    duplicate_group_count = int(
        df.loc[duplicate_mask]
        .drop_duplicates()
        .shape[0]
    )

    return [
        {
            "table_name": table_name,
            "duplicate_group_count": (
                duplicate_group_count
            ),
            "affected_rows": affected_rows,
        }
    ]


# =========================================================
# 6. TEXT VARIANTS
# =========================================================

def check_text_variants(
    table_name: str,
    df: pd.DataFrame,
) -> List[Dict]:
    """Find raw spellings that collapse to the same normalized text value."""
    rows = []

    for column in df.columns:
        series = df[column]

        # Skip columns that are clearly numeric.
        if pd.api.types.is_numeric_dtype(
            series
        ):
            continue

        values = _effective_non_missing(
            series
        )

        if len(values) == 0:
            continue

        raw_text = values.astype(
            "string"
        )

        comparison_key = (
            raw_text
            .str.strip()
            .str.replace(
                r"\s+",
                " ",
                regex=True,
            )
            .str.lower()
        )

        temp = pd.DataFrame(
            {
                "raw": raw_text,
                "comparison_key": (
                    comparison_key
                ),
            }
        )

        for key, group in temp.groupby(
            "comparison_key",
            dropna=True,
        ):
            variants = (
                group["raw"]
                .drop_duplicates()
                .tolist()
            )

            if len(variants) <= 1:
                continue

            rows.append(
                {
                    "table_name": (
                        table_name
                    ),
                    "column_name": column,
                    "issue_type": (
                        "TEXT_VARIANT"
                    ),
                    "comparison_key": key,
                    "variant_count": len(
                        variants
                    ),
                    "affected_rows": int(
                        len(group)
                    ),
                    "variants": " | ".join(
                        map(str, variants[:10])
                    ),
                }
            )

    return rows


# =========================================================
# 7. WHITESPACE
# =========================================================

def check_whitespace_issues(
    table_name: str,
    df: pd.DataFrame,
) -> List[Dict]:
    """Measure spaces and hidden tab/newline characters that can break equality."""
    rows = []

    for column in df.columns:
        if pd.api.types.is_numeric_dtype(
            df[column]
        ):
            continue

        series = (
            df[column]
            .astype("string")
        )

        leading_trailing = int(
            (
                series.notna()
                & (
                    series
                    != series.str.strip()
                )
            ).sum()
        )

        repeated_space = int(
            series.str.contains(
                r" {2,}",
                regex=True,
                na=False,
            ).sum()
        )

        tab_newline = int(
            series.str.contains(
                r"[\t\r\n]",
                regex=True,
                na=False,
            ).sum()
        )

        total_issue_mask = (
            (
                series.notna()
                & (
                    series
                    != series.str.strip()
                )
            )
            | series.str.contains(
                r" {2,}",
                regex=True,
                na=False,
            )
            | series.str.contains(
                r"[\t\r\n]",
                regex=True,
                na=False,
            )
        )

        total_issue_count = int(
            total_issue_mask.sum()
        )

        if total_issue_count == 0:
            continue

        rows.append(
            {
                "table_name": table_name,
                "column_name": column,
                "leading_trailing_space_count": (
                    leading_trailing
                ),
                "repeated_space_count": (
                    repeated_space
                ),
                "tab_newline_count": (
                    tab_newline
                ),
                "total_whitespace_issue_count": (
                    total_issue_count
                ),
            }
        )

    return rows


# =========================================================
# 8. COLUMN STRUCTURE
# =========================================================

def check_column_structure(
    table_name: str,
    df: pd.DataFrame,
) -> List[Dict]:
    """Flag unusable or constant columns as review signals, not deletions."""
    rows = []

    for column in df.columns:
        effective_values = (
            _effective_non_missing(
                df[column]
            )
        )

        non_missing_count = int(
            len(effective_values)
        )

        unique_non_missing_count = int(
            effective_values.nunique(
                dropna=True
            )
        )

        issue_type = None

        if non_missing_count == 0:
            issue_type = "ALL_MISSING"

        elif unique_non_missing_count == 1:
            issue_type = "CONSTANT_COLUMN"

        if issue_type:
            rows.append(
                {
                    "table_name": (
                        table_name
                    ),
                    "column_name": column,
                    "issue_type": (
                        issue_type
                    ),
                    "non_missing_count": (
                        non_missing_count
                    ),
                    "unique_non_missing_count": (
                        unique_non_missing_count
                    ),
                }
            )

    return rows


# =========================================================
# 9. DATATYPE SUGGESTIONS
# =========================================================

def suggest_datatypes(
    table_name: str,
    df: pd.DataFrame,
) -> List[Dict]:
    """Apply datatype inference consistently and format its evidence for reports."""
    rows = []

    for column in df.columns:
        result = infer_silver_datatype(
            df[column],
            column,
        )

        rows.append(
            {
                "table_name": table_name,
                "column_name": column,
                "suggested_silver_type": (
                    result[
                        "suggested_silver_type"
                    ]
                ),
                "numeric_ratio": (
                    result["numeric_ratio"]
                ),
                "date_ratio": (
                    result["date_ratio"]
                ),
            }
        )

    return rows


# =========================================================
# 10. DATATYPE ISSUES
# =========================================================

def check_datatype_issues(
    table_name: str,
    df: pd.DataFrame,
    datatype_suggestions: List[Dict],
) -> List[Dict]:
    """Find source values that violate a proposed type; this does not clean them."""
    rows = []

    suggestion_map = {
        item["column_name"]: (
            item["suggested_silver_type"]
        )
        for item in datatype_suggestions
    }

    for column in df.columns:
        suggested_type = (
            suggestion_map[column]
        )

        values = _effective_non_missing(
            df[column]
        )

        if (
            len(values) == 0
            or suggested_type == "string"
        ):
            continue

        invalid_mask = pd.Series(
            False,
            index=values.index,
        )

        text_values = (
            values.astype("string")
            .str.strip()
        )

        if suggested_type in (
            "integer",
            "decimal",
        ):
            parsed = pd.to_numeric(
                text_values.str.replace(
                    ",",
                    "",
                    regex=False,
                ),
                errors="coerce",
            )

            invalid_mask = (
                parsed.isna()
            )

            if suggested_type == "integer":
                valid_numeric = parsed.notna()
                invalid_mask = (
                    invalid_mask
                    | (
                        valid_numeric
                        & ((parsed % 1) != 0)
                    )
                )

        elif suggested_type in (
            "date",
            "datetime",
        ):
            parsed = pd.to_datetime(
                text_values,
                errors="coerce",
            )

            invalid_mask = (
                parsed.isna()
            )

        elif suggested_type == "boolean":
            normalized = (
                text_values.str.lower()
            )

            valid_boolean_values = {
                "true", "false",
                "yes", "no",
                "y", "n",
                "0", "1",
            }

            invalid_mask = (
                ~normalized.isin(
                    valid_boolean_values
                )
            )

        invalid_values = (
            values[invalid_mask]
            .astype("string")
        )

        if len(invalid_values) == 0:
            continue

        rows.append(
            {
                "table_name": table_name,
                "column_name": column,
                "suggested_silver_type": (
                    suggested_type
                ),
                "invalid_value_count": int(
                    len(invalid_values)
                ),
                "invalid_examples": " | ".join(
                    invalid_values
                    .drop_duplicates()
                    .head(10)
                    .tolist()
                ),
            }
        )

    return rows
