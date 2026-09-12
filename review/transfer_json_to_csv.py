import json
import pandas as pd

# 1. Đọc JSON
with open("./review/silver_schema_review.json", "r", encoding="utf-8") as f:
    data = json.load(f)

rows = []

# 2. Duyệt từng table
for table_name, table_info in data.items():

    table_metadata = table_info["table"]
    columns = table_info["columns"]

    # PK
    primary_keys = set(table_metadata.get("primary_key", []))

    # FK
    foreign_keys = {}

    for fk in table_metadata.get("foreign_keys", []):
        foreign_keys[fk["column"]] = fk["references"]

    # 3. Duyệt từng column
    for column_key, column_info in columns.items():

        column_name = column_info["name"]

        rows.append({
            "table_name": table_name,
            "column_name": column_name,
            "datatype": column_info["datatype"],
            "nullable": column_info["nullable"],
            "is_pk": column_name in primary_keys,
            "is_fk": column_name in foreign_keys,
            "fk_reference": foreign_keys.get(column_name)
        })

# 4. Tạo DataFrame
df = pd.DataFrame(rows)

# 5. Xuất CSV
df.to_csv(
    "./review/silver_schema_review.csv",
    index=False,
    encoding="utf-8-sig"
)

print("Done!")
print(f"Tables: {df['table_name'].nunique()}")
print(f"Columns: {len(df)}")