# Data Quality Decision Guide

`data_quality_decisions.csv` is the analyst decision surface.

## Decision

| Decision | Meaning | Build behavior |
|---|---|---|
| `FIX` | Confirmed issue; apply an approved automated action | Transformation runs |
| `KEEP` | Reviewed and accepted as valid/acceptable business data | Data remains unchanged |
| `BLOCK` | Unsafe to decide automatically | Build stops |

`KEEP` and `BLOCK` require a short `reason`.

## Suggested automatic actions

| Issue type | Suggested FIX action | Notes |
|---|---|---|
| `MISSING_VALUE` | `STANDARDIZE_NULL_MARKERS` | Converts configured fake-NULL markers only. It does not impute real NULLs. |
| `WHITESPACE` | `STANDARDIZE_TEXT` | Trim/collapse whitespace only in the approved column. |
| `TEXT_VARIANT` | `APPLY_VALUE_MAPPING` | Requires a business mapping in `standardization_rules.json`. |
| `EXACT_DUPLICATE` | `REMOVE_EXACT_DUPLICATES` | Removes exact duplicate rows only when analyst-approved. |
| `DATATYPE_PARSE` | `REVIEW_SCHEMA_DATATYPE` | Resolve by reviewing datatype in `silver_schema_review.csv`. |
| `ALL_MISSING_COLUMN` | `REVIEW_SCHEMA` | Decide business meaning / nullable / whether the field belongs in Silver. |
| `CONSTANT_COLUMN` | `REVIEW_SCHEMA` | Constant data is not automatically an error. |

## Hard constraints still win

A `KEEP` decision cannot contradict the schema contract.

Examples:

- If `nullable=false`, remaining NULL values still fail validation.
- If a column/composite is declared as a PK, duplicate PK values still fail validation.
- If a reviewed datatype is `integer`, values that cannot be converted to integer still fail.

If the business wants to keep those values, revise the schema/grain decision before compiling the contract.
