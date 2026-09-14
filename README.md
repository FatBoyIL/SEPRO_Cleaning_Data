# 🧹 SEPRO Data Cleaning Project

### Turning raw business data into trusted, analysis-ready data

Real-world data can contain missing values, inconsistent text, duplicates, incorrect datatypes, and unclear relationships.

This project shows how I handle those issues as a **Data Analyst** for Silver Layer belong to Medallion architecture.

**[Bronze Layer (Raw Data) -> Silver Layer (Validated Data) -> Gold Layer (Enriched Data)]**

> **Machine detects → Analyst decides → Pipeline executes → Validation proves.**

The final output is a SQL file that I can review before running manually in SSMS.

```mermaid
flowchart LR
    A["🗃️ Bronze Data"] --> B["🔎 run_review.py"]
    B --> C["🧠 Analyst Review"]
    C --> D["📜 review_to_json.py"]
    D --> E["⚙️ build_silver.py"]
    E --> F["📄 build_silver.sql"]
    F --> G["👀 Human Review"]
    G --> H["🥈 Execute in SSMS"]
```

---

## ▶️ How to Use

### 1. Profile Bronze and create review files

```powershell
python run_review.py
```

Review:

```text
review/silver_schema_review.csv
review/data_quality_decisions.csv
```

In `data_quality_decisions.csv`:

- `FIX` → apply an approved transformation.
- `KEEP` → keep valid/acceptable business data.
- `BLOCK` → stop because more investigation is required.

Examples:

```text
Missing optional phone     → KEEP
Whitespace in name         → FIX / STANDARDIZE_TEXT
Confirmed duplicate import → FIX / REMOVE_EXACT_DUPLICATES
Conflicting records        → BLOCK
```

---

### 2. Compile analyst decisions

```powershell
python review_to_json.py
```

Output:

```text
review/silver_review_contract.json
```

```text
CSV  = human review
JSON = machine contract
```

---

### 3. Generate the Silver SQL file

```powershell
python build_silver.py
```

Output:

```text
output/build_silver.sql
```

`build_silver.py` **does not connect to the database**.

The generated SQL contains:

- `CREATE SCHEMA silver`
- explicit Silver table definitions
- reviewed datatype / nullable rules
- approved cleaning transformations
- approved exact-duplicate removal
- optional value mappings
- optional VND currency conversion
- primary keys
- foreign keys
- a relationship summary
- hard validation guards
- post-build validation queries

If a reviewed issue is still `BLOCK`, SQL generation stops.

---

### 4. Review SQL before execution

Open:

```text
output/build_silver.sql
```

Check:

```text
Bronze → Silver mapping
column datatypes
NULL rules
FIX transformations
PKs
FKs
relationships
validation queries
```

The top of the SQL file contains a relationship summary such as:

```text
silver.customers.customer_id
    1 -------------------- * silver.sales_orders.customer_id
```

---

### 5. Execute manually in SSMS

Only after reviewing the SQL file, execute it manually in SQL Server Management Studio.

The generated script is intentionally non-destructive:

> If a target `silver.*` table already exists, execution stops instead of silently dropping or replacing it.

The SQL is wrapped in a transaction. Hard validation failures trigger rollback.

---

## 🧠 Review Rules

### Schema review

Use:

```text
review/silver_schema_review.csv
```

Main fields:

```text
source_column      = original Bronze column
column_name        = final Silver column
datatype           = reviewed datatype
nullable           = business NULL rule
is_pk / pk_order   = reviewed table grain
is_fk / reference  = reviewed relationship
```

### Data Quality review

Use:

```text
review/data_quality_decisions.csv
```

Common FIX actions:

| Issue             | Action                     |
| ----------------- | -------------------------- |
| Fake NULL markers | `STANDARDIZE_NULL_MARKERS` |
| Whitespace        | `STANDARDIZE_TEXT`         |
| Text variants     | `APPLY_VALUE_MAPPING`      |
| Exact duplicates  | `REMOVE_EXACT_DUPLICATES`  |

`KEEP` does not override schema constraints. If a field is reviewed as `NOT NULL` or as a PK, the generated SQL still validates that rule.

---

## 📁 Main Project Flow

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
    ↓
output/build_silver.sql
    ↓
manual review
    ↓
manual execution in SSMS
```

---

## 🎯 What This Project Demonstrates

- 🔎 profile before cleaning
- 🧠 separate anomalies from business meaning
- 🧩 define grain before keys
- 📝 keep analyst decisions explicit
- 🧬 avoid blindly deleting duplicates
- ⚙️ separate human review from machine execution
- 🛡️ keep database deployment under human control
- ✅ validate before trusting Silver

> **The goal is not only clean data. It is data transformations I can explain, review, and safely approve before execution.**
