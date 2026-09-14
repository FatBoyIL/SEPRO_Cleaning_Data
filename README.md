## 🧹 SEPRO Data Cleaning Project

### Turning raw business data into trusted, analysis-ready data

Real-world data can contain missing values, inconsistent text, duplicates, incorrect datatypes, and unclear relationships.
# 📊 SEPRO Enterprise Data Warehouse – Silver Layer Design (2026)

> **Clean Data for Better Decisions:** A Silver Layer database design built to standardize, cleanse, and integrate cross-functional data across SEPRO ECO CLEAN CO., LTD., providing a reliable foundation for analytics and business reporting.

---

## 📐 System Architecture & Scale

* **Schema Size:** 32 Tables | 52 Relationships
* **Data Architecture:** Silver Layer — integrated and standardized datasets prepared for analytics and reporting
* **Functional Domains:**

| Domain / Module                      | Core Tables                                                                       | Business Purpose                                                                                           |
| :----------------------------------- | :-------------------------------------------------------------------------------- | :--------------------------------------------------------------------------------------------------------- |
| **Master Data**                      | `departments`, `employees`, `customers`, `suppliers`, `products`, `warehouses`    | Maintains core reference and master data to ensure consistency and data integrity across the organization. |
| **Marketing & Lead Management**      | `marketing_campaigns`, `marketing_daily`, `leads`                                 | Tracks campaign performance, advertising spend, ROI, and the full lead lifecycle.                          |
| **Sales & CRM**                      | `sales_activities`, `quotations`, `sales_orders`, `sales_order_lines`             | Manages sales activities, quotations, customer orders, and actual sales performance.                       |
| **Purchasing & Supplier Management** | `purchase_orders`, `purchase_order_lines`, `product_supplier_map`                 | Manages procurement activities, supplier relationships, and product purchase costs.                        |
| **Inventory & Operations**           | `inventory_movements`, `inventory_daily_snapshot`, `inventory_policy_history`     | Tracks inventory movements, stock levels, reorder points, and inventory policies.                          |
| **Finance & Accounting**             | `invoices`, `invoice_lines`, `payments`, `supplier_invoices`, `supplier_payments` | Supports Accounts Receivable (AR), Accounts Payable (AP), payment reconciliation, and cash flow analysis.  |
| **Quality & After-Sales**            | `qa_events`, `process_events`, `after_sales_cases`                                | Tracks quality incidents, operational process events, customer complaints, and after-sales cases.          |

---

## 🛠️ Key ERD Design Highlights

* **Normalized Data Model:**
  The schema follows a normalized relational design with clearly defined Primary Key (PK) and Foreign Key (FK) constraints to maintain referential integrity and minimize data redundancy.

* **End-to-End Data Integration:**
  The model connects the complete business flow from **Marketing Lead → Sales Order → Inventory → Invoice → Payment**, enabling cross-functional analysis across multiple departments.

* **Analytics-Ready Silver Layer:**
  Data is cleansed, standardized, validated, and relationally integrated, making the Silver Layer suitable as a trusted source for BI reporting and downstream analytical models.

* **Cross-Domain Traceability:**
  Relationships between customers, products, suppliers, sales transactions, inventory movements, invoices, and payments allow business activities to be traced across the full operational lifecycle.

* **Designed for BI & Data Warehouse Workflows:**
  The Silver Layer can serve as the standardized source for BI platforms such as **Power BI** and **Looker Studio**, or as an upstream source for a downstream **Gold Layer, Data Mart, Lakehouse, or Enterprise Data Warehouse**.

---

## 🖼️ Entity Relationship Diagram (ERD)

![SEPRO Enterprise Data Warehouse ERD](https://github.com/user-attachments/assets/6f6e15ef-c3d4-45a7-9e76-2aefd8eaf44e)

## 🧠This project shows how I handle those issues as a Data Analyst for Silver Layer belong to Medallion architecture.

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
