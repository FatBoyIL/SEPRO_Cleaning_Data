# Decision Review Upgrade — Migration

This upgrade changes the Silver workflow from:

```text
Review JSON -> Build
```

to:

```text
run_review.py
    ↓
silver_schema_review.csv
+ data_quality_decisions.csv
    ↓
review_to_json.py
    ↓
silver_review_contract.json
    ↓
build_silver.py
```

## Files changed / added

```text
README.md
run_review.py
review_to_json.py                 NEW
build_silver.py
profiling/schema_proposal.py
standardization/decision_policy.py NEW
standardization/standardize_silver.py
standardization/landing_data/null_standardization.py
standardization/landing_data/text_standardization.py
validation/validate_silver.py
review/DECISION_GUIDE.md           NEW
```

## New analyst decisions

`review/data_quality_decisions.csv` uses:

```text
FIX   = apply approved transformation
KEEP  = preserve reviewed business data
BLOCK = stop build for investigation
```

`KEEP` and `BLOCK` require a reason.

## New run sequence

```powershell
python run_review.py
```

Edit:

```text
review/silver_schema_review.csv
review/data_quality_decisions.csv
```

Compile:

```powershell
python review_to_json.py
```

Build:

```powershell
python build_silver.py
```

`build_silver.py` now reads:

```text
review/silver_review_contract.json
```
