# =========================================================
# DATABASE CONFIG
# =========================================================
# SQL Server instance and database read by the profiling pipeline.
SERVER = r"DESKTOP-4F2KL18\MSSQLSERVER1"
DATABASE = "SEPRO_Master_Prod"

# =========================================================
# BRONZE TABLE CONFIG
# =========================================================

# Guardrail for detecting an incomplete Bronze inventory before execution.
EXPECTED_TABLE_COUNT = 32

# The source inventory iterated by Phase 1; add or remove tables here intentionally.
BRONZE_TABLES = [
    "after_sales_cases_raw",
    "business_interventions",
    "customers_raw",
    "departments",
    "employees",
    "fx_rates_daily",
    "inventory_daily_snapshot_raw",
    "inventory_movements_raw",
    "inventory_policy_history",
    "invoice_lines_raw",
    "invoices_raw",
    "leads_raw",
    "marketing_campaigns",
    "marketing_daily_raw",
    "payments_raw",
    "process_events_raw",
    "product_supplier_map_raw",
    "products_raw",
    "purchase_order_lines_raw",
    "purchase_orders_raw",
    "qa_events_raw",
    "quotation_lines_raw",
    "quotations_raw",
    "sales_activities_raw",
    "sales_order_lines_raw",
    "sales_orders_raw",
    "shipment_lines_raw",
    "shipments_raw",
    "supplier_invoices_raw",
    "supplier_payments_raw",
    "suppliers_raw",
    "warehouses",
]

# =========================================================
# MISSING VALUE RULES
# =========================================================
#
# Các giá trị text dưới đây được xem là "fake NULL".
#
# Trước khi so sánh, profiling/data_quality.py sẽ:
#   1. strip()      -> bỏ khoảng trắng đầu/cuối
#   2. lowercase()  -> chuyển về chữ thường
#
# Ví dụ:
#   " N/A "   -> "n/a"  -> Missing
#   "NULL"    -> "null" -> Missing
#
# Lưu ý:
#   - "unknown" KHÔNG được xem là NULL toàn cục.
#   - "not available" KHÔNG được xem là NULL toàn cục.
#
# Hai giá trị trên có thể mang business meaning riêng,
# nên nếu cần xử lý sẽ thực hiện theo từng column ở bước
# Standardization sau này.
# =========================================================

NULL_MARKERS = {
    "",
    "na",
    "n/a",
    "n.a.",
    "null",
    "none",
    "empty",
    "-",
    "--",
}