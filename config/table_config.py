# =========================================================
# DATABASE CONFIG
# =========================================================
# SQL Server instance and database read by the profiling pipeline.
SERVER = r"localhost\SQLEXPRESS"
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
#
# Sau khi:
# strip()
# lowercase()
#
# Các value này được xem là Missing Value.
# =========================================================

NULL_MARKERS = {
    "",
    "n/a",
    "null",
    "empty",
}