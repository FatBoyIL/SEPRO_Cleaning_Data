import json
from pathlib import Path

import pandas as pd


# ============================================================
# CONFIG
# ============================================================

JSON_PATH = Path("./review/silver_schema_review.json")
CSV_PATH = Path("./review/silver_schema_review.csv")
SQL_PATH = Path("./review/silver_schema_review.sql")

DATABASE_NAME = "SEPRO_Master_Prod"
SOURCE_SCHEMA = "bronze"

# Số dòng sample lấy từ mỗi Bronze table để review.
SAMPLE_ROWS = 200


# ============================================================
# SQL HELPERS
# ============================================================

def quote_sql_identifier(value: str) -> str:
    """
    Escape identifier theo cú pháp SQL Server.

    Ví dụ:
        customer_id -> [customer_id]
    """
    return f"[{str(value).replace(']', ']]')}]"


def generate_review_sql(df: pd.DataFrame, output_path: Path) -> None:
    """
    Tạo file SQL đi kèm CSV review.

    Mỗi table xuất hiện trong CSV sẽ có:
    1. SELECT COUNT_BIG(*) để xem tổng số record.
    2. SELECT TOP (...) các column liên quan để xem dữ liệu Bronze thực tế.

    SQL chỉ đọc dữ liệu, không UPDATE/DELETE Bronze.
    """

    sql_lines = [
        "-- ============================================================",
        "-- SEPRO SILVER SCHEMA REVIEW",
        "-- AUTO-GENERATED FROM silver_schema_review.csv",
        "-- SQL Server / READ ONLY",
        "-- ============================================================",
        "",
    ]

    # Mỗi table chỉ generate một block SQL.
    for table_name, table_df in df.groupby("table_name", sort=False):

        database = quote_sql_identifier(DATABASE_NAME)
        schema = quote_sql_identifier(SOURCE_SCHEMA)
        table = quote_sql_identifier(table_name)

        full_table_name = f"{database}.{schema}.{table}"

        sql_lines.extend([
            "",
            "-- ============================================================",
            f"-- TABLE: {SOURCE_SCHEMA}.{table_name}",
            "-- ============================================================",
            "",
        ])

        # ------------------------------------------------------------
        # Query 1: kiểm tra tổng số record trong Bronze table.
        # ------------------------------------------------------------

        sql_lines.extend([
            "-- Total rows",
            "SELECT",
            "    COUNT_BIG(*) AS total_rows",
            f"FROM {full_table_name};",
            "",
        ])

        # ------------------------------------------------------------
        # Query 2: lấy dữ liệu thực tế để đối chiếu với schema proposal.
        #
        # source_column_name = tên thật trong Bronze
        # column_name        = tên đề xuất cho Silver
        # ------------------------------------------------------------

        sql_lines.extend([
            f"-- Sample Bronze data for review",
            f"SELECT TOP ({SAMPLE_ROWS})",
        ])

        select_columns = []

        for _, row in table_df.iterrows():

            source_column = str(row["source_column_name"])
            silver_column = str(row["column_name"])

            source_sql = quote_sql_identifier(source_column)
            silver_sql = quote_sql_identifier(silver_column)

            # Nếu tên Bronze và Silver khác nhau, alias giúp nhìn rõ
            # mapping khi review trực tiếp kết quả SQL.
            if source_column != silver_column:
                expression = f"    {source_sql} AS {silver_sql}"
            else:
                expression = f"    {source_sql}"

            metadata = []

            if bool(row["is_pk"]):
                metadata.append("PK")

            if bool(row["is_fk"]):
                fk_reference = row.get("fk_reference")

                if pd.notna(fk_reference):
                    metadata.append(f"FK -> {fk_reference}")
                else:
                    metadata.append("FK")

            metadata.append(
                f"proposed_type={row['datatype']}"
            )

            metadata.append(
                f"nullable={row['nullable']}"
            )

            comment = " | ".join(metadata)

            select_columns.append(
                (expression, comment)
            )

        for index, (expression, comment) in enumerate(select_columns):

            comma = "," if index < len(select_columns) - 1 else ""

            sql_lines.append(
                f"{expression}{comma}  -- {comment}"
            )

        sql_lines.extend([
            f"FROM {full_table_name};",
            "",
        ])

    output_path.write_text(
        "\n".join(sql_lines),
        encoding="utf-8-sig"
    )


# ============================================================
# MAIN
# ============================================================

# 1. Đọc JSON schema proposal.
with JSON_PATH.open(
    "r",
    encoding="utf-8"
) as f:
    data = json.load(f)


rows = []


# 2. Duyệt từng table.
for table_name, table_info in data.items():

    table_metadata = table_info["table"]
    columns = table_info["columns"]

    # Proposed Primary Key.
    primary_keys = set(
        table_metadata.get("primary_key", [])
    )

    # Proposed Foreign Keys.
    foreign_keys = {}

    for fk in table_metadata.get(
        "foreign_keys",
        []
    ):
        foreign_keys[fk["column"]] = fk["references"]

    # 3. Duyệt từng column.
    for column_key, column_info in columns.items():

        # column_key:
        # tên column gốc trong Bronze.
        source_column_name = column_key

        # column_info["name"]:
        # tên column được đề xuất cho Silver.
        silver_column_name = column_info["name"]

        rows.append({
            "table_name": table_name,

            # Giữ tên Bronze để SQL có thể truy vấn đúng source.
            "source_column_name": source_column_name,

            # Tên dự kiến sau khi đưa sang Silver.
            "column_name": silver_column_name,

            "datatype": column_info["datatype"],
            "nullable": column_info["nullable"],

            "is_pk": silver_column_name in primary_keys,

            "is_fk": (
                silver_column_name in foreign_keys
            ),

            "fk_reference": foreign_keys.get(
                silver_column_name
            )
        })


# 4. Tạo DataFrame.
df = pd.DataFrame(rows)


# 5. Xuất CSV review.
df.to_csv(
    CSV_PATH,
    index=False,
    encoding="utf-8-sig"
)


# 6. Tạo SQL review dựa trên chính nội dung CSV/DataFrame vừa xuất.
generate_review_sql(
    df=df,
    output_path=SQL_PATH
)