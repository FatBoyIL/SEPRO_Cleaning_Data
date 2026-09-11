# =========================================================
# DATABASE CONFIG
# =========================================================
SERVER = r"DESKTOP-4F2KL18\MSSQLSERVER1"
DATABASE = "SEPRO_Master_Prod"

# =========================================================
# BRONZE TABLE CONFIG
# =========================================================

EXPECTED_TABLE_COUNT = 32


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