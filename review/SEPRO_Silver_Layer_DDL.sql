/*==========================================================================
  SEPRO DATA ANALYST PORTFOLIO - SILVER LAYER DDL
  SQL Server

  Design basis:
  - reviewed silver_schema_review.json
  - approved composite/special PK decisions
  - SEPRO business rules and logical ERD

  DATE STANDARD
  - SQL Server DATE/DATETIME2 values do NOT store a display format.
  - All business date columns are stored as DATE; timestamps as DATETIME2(0).
  - Session input convention is DMY via SET DATEFORMAT dmy.
  - For dd/mm/yyyy display use: CONVERT(CHAR(10), date_column, 103)
  - For cleaning/import from dd/mm/yyyy text use: TRY_CONVERT(DATE, raw_value, 103)

  NOTE: Business-rule failures should be handled in Python validation /
        quarantine before loading Silver, rather than by destructive DDL.
==========================================================================*/

SET NOCOUNT ON;
SET XACT_ABORT ON;
SET DATEFORMAT dmy;
GO

IF NOT EXISTS (SELECT 1 FROM sys.schemas WHERE name = N'silver')
    EXEC(N'CREATE SCHEMA silver');
GO


-- ======================================================================
-- A. MASTER / REFERENCE
-- ======================================================================

CREATE TABLE silver.[departments] (
    [department_id] NVARCHAR(64) NOT NULL,
    [department_name] NVARCHAR(1000) NOT NULL,
    CONSTRAINT [PK_departments] PRIMARY KEY ([department_id])
);
GO

CREATE TABLE silver.[employees] (
    [employee_id] NVARCHAR(64) NOT NULL,
    [employee_name] NVARCHAR(1000) NOT NULL,
    [department_id] NVARCHAR(64) NOT NULL,
    [job_title] NVARCHAR(255) NOT NULL,
    [hire_date] DATE NOT NULL,
    [termination_date] DATE NULL,
    CONSTRAINT [PK_employees] PRIMARY KEY ([employee_id])
);
GO

CREATE TABLE silver.[customers] (
    [customer_id] NVARCHAR(64) NOT NULL,
    [company_name] NVARCHAR(1000) NOT NULL,
    [tax_id] NVARCHAR(64) NULL,
    [industry] NVARCHAR(100) NOT NULL,
    [province] NVARCHAR(100) NOT NULL,
    [customer_segment] NVARCHAR(100) NOT NULL,
    [company_size_band] NVARCHAR(100) NOT NULL,
    [credit_risk] NVARCHAR(100) NOT NULL,
    [default_payment_term] NVARCHAR(255) NOT NULL,
    [customer_since] DATE NOT NULL,
    CONSTRAINT [PK_customers] PRIMARY KEY ([customer_id])
);
GO

CREATE TABLE silver.[products] (
    [product_id] NVARCHAR(64) NOT NULL,
    [sku] NVARCHAR(64) NOT NULL,
    [product_name] NVARCHAR(1000) NOT NULL,
    [product_category] NVARCHAR(100) NOT NULL,
    [product_family] NVARCHAR(100) NOT NULL,
    [brand] NVARCHAR(100) NOT NULL,
    [source_country] NVARCHAR(255) NOT NULL,
    [standard_cost_vnd] BIGINT NOT NULL,
    [list_price_vnd] BIGINT NOT NULL,
    [shelf_life_days] INT NULL,
    [criticality] NVARCHAR(100) NOT NULL,
    [base_reorder_point_qty] INT NOT NULL,
    [base_safety_stock_qty] INT NOT NULL,
    [popularity_rank] INT NOT NULL,
    CONSTRAINT [PK_products] PRIMARY KEY ([product_id])
);
GO

CREATE TABLE silver.[suppliers] (
    [supplier_id] NVARCHAR(64) NOT NULL,
    [supplier_name] NVARCHAR(1000) NOT NULL,
    [country] NVARCHAR(100) NOT NULL,
    [currency] NVARCHAR(10) NOT NULL,
    [payment_term] NVARCHAR(100) NOT NULL,
    [incoterm] NVARCHAR(100) NOT NULL,
    [planned_lead_time_days] INT NOT NULL,
    [baseline_otif] DECIMAL(9,4) NOT NULL,
    [baseline_defect_rate] DECIMAL(9,4) NOT NULL,
    [default_freight_mode] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_suppliers] PRIMARY KEY ([supplier_id])
);
GO

CREATE TABLE silver.[warehouses] (
    [warehouse_id] NVARCHAR(64) NOT NULL,
    [warehouse_name] NVARCHAR(1000) NOT NULL,
    [city] NVARCHAR(255) NOT NULL,
    [province] NVARCHAR(100) NOT NULL,
    [warehouse_type] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_warehouses] PRIMARY KEY ([warehouse_id])
);
GO

CREATE TABLE silver.[marketing_campaigns] (
    [campaign_id] NVARCHAR(64) NOT NULL,
    [campaign_name] NVARCHAR(1000) NOT NULL,
    [channel] NVARCHAR(100) NOT NULL,
    [start_date] DATE NOT NULL,
    [end_date] DATE NOT NULL,
    [objective] NVARCHAR(100) NOT NULL,
    [planned_daily_budget_vnd] BIGINT NOT NULL,
    CONSTRAINT [PK_marketing_campaigns] PRIMARY KEY ([campaign_id])
);
GO

CREATE TABLE silver.[fx_rates_daily] (
    [date] DATE NOT NULL,
    [currency] NVARCHAR(10) NOT NULL,
    [vnd_per_currency] DECIMAL(19,6) NOT NULL,
    CONSTRAINT [PK_fx_rates_daily] PRIMARY KEY ([date], [currency])
);
GO

CREATE TABLE silver.[product_supplier_map] (
    [map_id] NVARCHAR(64) NOT NULL,
    [product_id] NVARCHAR(64) NOT NULL,
    [supplier_id] NVARCHAR(64) NOT NULL,
    [supplier_role] NVARCHAR(100) NOT NULL,
    [effective_from] DATE NOT NULL,
    [effective_to] DATE NULL,
    [currency] NVARCHAR(10) NOT NULL,
    [unit_cost_local] DECIMAL(19,4) NOT NULL,
    [moq_qty] INT NOT NULL,
    [contract_lead_time_days] INT NOT NULL,
    [expected_defect_rate] DECIMAL(9,4) NOT NULL,
    [freight_mode] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_product_supplier_map] PRIMARY KEY ([map_id]),
    CONSTRAINT [UQ_product_supplier_map_business] UNIQUE ([product_id], [supplier_id], [effective_from])
);
GO


-- ======================================================================
-- B. LEAD TO ORDER / SALES
-- ======================================================================

CREATE TABLE silver.[leads] (
    [lead_id] NVARCHAR(64) NOT NULL,
    [created_at] DATETIME2(0) NOT NULL,
    [customer_id] NVARCHAR(64) NULL,
    [company_name] NVARCHAR(1000) NOT NULL,
    [industry] NVARCHAR(100) NOT NULL,
    [province] NVARCHAR(100) NOT NULL,
    [source_channel] NVARCHAR(255) NOT NULL,
    [campaign_id] NVARCHAR(64) NOT NULL,
    [product_interest_id] NVARCHAR(64) NOT NULL,
    [inquiry_type] NVARCHAR(100) NOT NULL,
    [company_size_band] NVARCHAR(100) NOT NULL,
    [pages_viewed_30d] INT NOT NULL,
    [form_completeness_pct] INT NOT NULL,
    [requested_quote_flag] BIT NOT NULL,
    [requested_sample_flag] BIT NOT NULL,
    [budget_status] NVARCHAR(100) NULL,
    [purchase_timeline_days] INT NOT NULL,
    [assigned_sales_id] NVARCHAR(64) NOT NULL,
    [lifecycle_status] NVARCHAR(100) NOT NULL,
    [lost_reason] NVARCHAR(1000) NULL,
    [expected_value_vnd] BIGINT NOT NULL,
    [email] NVARCHAR(320) NULL,
    [phone] NVARCHAR(50) NULL,
    CONSTRAINT [PK_leads] PRIMARY KEY ([lead_id])
);
GO

CREATE TABLE silver.[sales_activities] (
    [activity_id] NVARCHAR(64) NOT NULL,
    [lead_id] NVARCHAR(64) NOT NULL,
    [employee_id] NVARCHAR(64) NOT NULL,
    [activity_datetime] DATETIME2(0) NOT NULL,
    [activity_type] NVARCHAR(100) NOT NULL,
    [outcome] NVARCHAR(100) NULL,
    [next_followup_date] DATE NULL,
    [data_entry_source] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_sales_activities] PRIMARY KEY ([activity_id])
);
GO

CREATE TABLE silver.[quotations] (
    [quotation_id] NVARCHAR(64) NOT NULL,
    [lead_id] NVARCHAR(64) NOT NULL,
    [customer_id] NVARCHAR(64) NULL,
    [quote_date] DATE NOT NULL,
    [version_no] INT NOT NULL,
    [quote_date_clean_reference] DATE NOT NULL,
    [valid_until] DATE NOT NULL,
    [salesperson_id] NVARCHAR(64) NOT NULL,
    [currency] NVARCHAR(10) NOT NULL,
    [fx_rate_to_vnd] DECIMAL(19,6) NOT NULL,
    [quotation_status] NVARCHAR(100) NOT NULL,
    [header_total_vnd] BIGINT NOT NULL,
    CONSTRAINT [PK_quotations] PRIMARY KEY ([quotation_id])
);
GO

CREATE TABLE silver.[quotation_lines] (
    [quotation_line_id] NVARCHAR(64) NOT NULL,
    [quotation_id] NVARCHAR(64) NOT NULL,
    [product_id] NVARCHAR(64) NOT NULL,
    [quantity] INT NOT NULL,
    [unit_price_local] DECIMAL(19,4) NOT NULL,
    [currency] NVARCHAR(10) NOT NULL,
    [discount_pct] DECIMAL(9,4) NOT NULL,
    [line_net_vnd] BIGINT NOT NULL,
    CONSTRAINT [PK_quotation_lines] PRIMARY KEY ([quotation_line_id])
);
GO

CREATE TABLE silver.[sales_orders] (
    [sales_order_id] NVARCHAR(64) NOT NULL,
    [quotation_id] NVARCHAR(64) NULL,
    [lead_id] NVARCHAR(64) NULL,
    [customer_id] NVARCHAR(64) NOT NULL,
    [order_date] DATE NOT NULL,
    [requested_delivery_date] DATE NOT NULL,
    [salesperson_id] NVARCHAR(64) NOT NULL,
    [payment_term] NVARCHAR(100) NOT NULL,
    [order_status] NVARCHAR(100) NOT NULL,
    [order_type] NVARCHAR(100) NOT NULL,
    [currency] NVARCHAR(10) NOT NULL,
    [fx_rate_to_vnd] DECIMAL(19,6) NOT NULL,
    CONSTRAINT [PK_sales_orders] PRIMARY KEY ([sales_order_id])
);
GO

CREATE TABLE silver.[sales_order_lines] (
    [sales_order_line_id] NVARCHAR(64) NOT NULL,
    [sales_order_id] NVARCHAR(64) NOT NULL,
    [product_id] NVARCHAR(64) NOT NULL,
    [quantity_ordered] INT NOT NULL,
    [unit_price_vnd] BIGINT NOT NULL,
    [unit_cost_vnd] BIGINT NOT NULL,
    [discount_pct] DECIMAL(9,4) NOT NULL,
    [promised_delivery_date] DATE NOT NULL,
    [line_status] NVARCHAR(100) NOT NULL,
    [quantity_shipped_to_date] INT NOT NULL,
    CONSTRAINT [PK_sales_order_lines] PRIMARY KEY ([sales_order_line_id])
);
GO

CREATE TABLE silver.[marketing_daily] (
    [date] DATE NOT NULL,
    [campaign_id] NVARCHAR(64) NOT NULL,
    [channel] NVARCHAR(100) NOT NULL,
    [impressions] INT NOT NULL,
    [clicks] INT NOT NULL,
    [sessions] INT NOT NULL,
    [spend_vnd] BIGINT NOT NULL,
    [leads_reported] INT NOT NULL,
    [source_platform] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_marketing_daily] PRIMARY KEY ([date], [campaign_id], [source_platform])
);
GO


-- ======================================================================
-- C. INVENTORY / PURCHASING / FULFILLMENT
-- ======================================================================

CREATE TABLE silver.[inventory_policy_history] (
    [product_id] NVARCHAR(64) NOT NULL,
    [effective_from] DATE NOT NULL,
    [effective_to] DATE NULL,
    [reorder_point_qty] INT NOT NULL,
    [safety_stock_qty] INT NOT NULL,
    [target_service_level] DECIMAL(9,4) NOT NULL,
    [annual_holding_rate_pct] DECIMAL(9,4) NOT NULL,
    CONSTRAINT [PK_inventory_policy_history] PRIMARY KEY ([product_id], [effective_from])
);
GO

CREATE TABLE silver.[inventory_daily_snapshot] (
    [snapshot_date] DATE NOT NULL,
    [warehouse_id] NVARCHAR(64) NOT NULL,
    [product_id] NVARCHAR(64) NOT NULL,
    [on_hand_qty] INT NOT NULL,
    [on_order_qty] INT NOT NULL,
    [backlog_qty] INT NOT NULL,
    [available_qty] INT NOT NULL,
    [unit_cost_vnd] BIGINT NOT NULL,
    [inventory_value_vnd] BIGINT NOT NULL,
    [stockout_flag] BIT NOT NULL,
    [reorder_point_qty] INT NOT NULL,
    [safety_stock_qty] INT NOT NULL,
    CONSTRAINT [PK_inventory_daily_snapshot] PRIMARY KEY ([snapshot_date], [warehouse_id], [product_id])
);
GO

CREATE TABLE silver.[inventory_movements] (
    [inventory_move_id] NVARCHAR(64) NOT NULL,
    [movement_date] DATE NOT NULL,
    [warehouse_id] NVARCHAR(64) NOT NULL,
    [product_id] NVARCHAR(64) NOT NULL,
    [movement_type] NVARCHAR(100) NOT NULL,
    [quantity_signed] INT NOT NULL,
    [unit_cost_vnd] DECIMAL(19,4) NOT NULL,
    [reference_document] NVARCHAR(1000) NOT NULL,
    [reference_line] NVARCHAR(255) NULL,
    [batch_no] NVARCHAR(64) NULL,
    [expiry_date] DATE NULL,
    CONSTRAINT [PK_inventory_movements] PRIMARY KEY ([inventory_move_id])
);
GO

CREATE TABLE silver.[purchase_orders] (
    [purchase_order_id] NVARCHAR(64) NOT NULL,
    [po_date] DATE NOT NULL,
    [supplier_id] NVARCHAR(64) NOT NULL,
    [buyer_id] NVARCHAR(64) NOT NULL,
    [expected_receipt_date] DATE NOT NULL,
    [actual_receipt_date] DATE NULL,
    [currency] NVARCHAR(10) NOT NULL,
    [fx_rate_to_vnd] DECIMAL(19,6) NOT NULL,
    [payment_term] NVARCHAR(100) NOT NULL,
    [incoterm] NVARCHAR(100) NOT NULL,
    [po_status] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_purchase_orders] PRIMARY KEY ([purchase_order_id])
);
GO

CREATE TABLE silver.[purchase_order_lines] (
    [purchase_order_line_id] NVARCHAR(64) NOT NULL,
    [purchase_order_id] NVARCHAR(64) NOT NULL,
    [product_id] NVARCHAR(64) NOT NULL,
    [quantity_ordered] INT NOT NULL,
    [unit_price_local] DECIMAL(19,4) NOT NULL,
    [currency] NVARCHAR(10) NOT NULL,
    [fx_rate_to_vnd] DECIMAL(19,6) NOT NULL,
    [merchandise_value_vnd] BIGINT NOT NULL,
    [freight_cost_vnd] BIGINT NOT NULL,
    [customs_cost_vnd] BIGINT NOT NULL,
    [expedite_cost_vnd] BIGINT NOT NULL,
    [supplier_role] NVARCHAR(100) NOT NULL,
    [contract_lead_time_days] INT NOT NULL,
    CONSTRAINT [PK_purchase_order_lines] PRIMARY KEY ([purchase_order_line_id])
);
GO

CREATE TABLE silver.[qa_events] (
    [qa_event_id] NVARCHAR(64) NOT NULL,
    [event_date] DATE NOT NULL,
    [purchase_order_id] NVARCHAR(64) NOT NULL,
    [purchase_order_line_id] NVARCHAR(64) NOT NULL,
    [product_id] NVARCHAR(64) NOT NULL,
    [supplier_id] NVARCHAR(64) NOT NULL,
    [qa_type] NVARCHAR(100) NOT NULL,
    [severity] NVARCHAR(100) NOT NULL,
    [defect_qty] INT NOT NULL,
    [estimated_quality_cost_vnd] BIGINT NOT NULL,
    [disposition] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_qa_events] PRIMARY KEY ([qa_event_id])
);
GO

CREATE TABLE silver.[shipments] (
    [shipment_id] NVARCHAR(64) NOT NULL,
    [sales_order_id] NVARCHAR(64) NOT NULL,
    [ship_date] DATE NOT NULL,
    [actual_delivery_date] DATE NOT NULL,
    [shipping_method] NVARCHAR(255) NOT NULL,
    [warehouse_id] NVARCHAR(64) NOT NULL,
    [freight_cost_vnd] BIGINT NOT NULL,
    [shipment_status] NVARCHAR(100) NOT NULL,
    [partial_shipment_flag] BIT NOT NULL,
    [tracking_no] NVARCHAR(64) NOT NULL,
    CONSTRAINT [PK_shipments] PRIMARY KEY ([shipment_id])
);
GO

CREATE TABLE silver.[shipment_lines] (
    [shipment_line_id] NVARCHAR(64) NOT NULL,
    [shipment_id] NVARCHAR(64) NOT NULL,
    [sales_order_line_id] NVARCHAR(64) NOT NULL,
    [product_id] NVARCHAR(64) NOT NULL,
    [quantity_shipped] INT NOT NULL,
    CONSTRAINT [PK_shipment_lines] PRIMARY KEY ([shipment_line_id])
);
GO


-- ======================================================================
-- D. FINANCE - ACCOUNTS RECEIVABLE
-- ======================================================================

CREATE TABLE silver.[invoices] (
    [invoice_id] NVARCHAR(64) NOT NULL,
    [sales_order_id] NVARCHAR(64) NOT NULL,
    [customer_id] NVARCHAR(64) NOT NULL,
    [invoice_date] DATE NOT NULL,
    [due_date] DATE NOT NULL,
    [payment_term] NVARCHAR(100) NOT NULL,
    [invoice_amount_vnd] BIGINT NOT NULL,
    [payment_status] NVARCHAR(100) NOT NULL,
    [source_system] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_invoices] PRIMARY KEY ([invoice_id])
);
GO

CREATE TABLE silver.[invoice_lines] (
    [invoice_line_id] NVARCHAR(64) NOT NULL,
    [invoice_id] NVARCHAR(64) NOT NULL,
    [sales_order_line_id] NVARCHAR(64) NOT NULL,
    [product_id] NVARCHAR(64) NOT NULL,
    [quantity_invoiced] INT NOT NULL,
    [unit_price_vnd] BIGINT NOT NULL,
    [discount_pct] DECIMAL(9,4) NOT NULL,
    [net_amount_vnd] BIGINT NOT NULL,
    [tax_amount_vnd] NVARCHAR(255) NOT NULL,
    [line_total_vnd] BIGINT NOT NULL,
    CONSTRAINT [PK_invoice_lines] PRIMARY KEY ([invoice_line_id])
);
GO

CREATE TABLE silver.[payments] (
    [payment_id] NVARCHAR(64) NOT NULL,
    [invoice_id] NVARCHAR(64) NOT NULL,
    [customer_id] NVARCHAR(64) NOT NULL,
    [payment_date] DATE NOT NULL,
    [paid_amount_vnd] BIGINT NOT NULL,
    [payment_method] NVARCHAR(100) NOT NULL,
    [payment_stage] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_payments] PRIMARY KEY ([payment_id])
);
GO


-- ======================================================================
-- E. FINANCE - ACCOUNTS PAYABLE
-- ======================================================================

CREATE TABLE silver.[supplier_invoices] (
    [supplier_invoice_id] NVARCHAR(64) NOT NULL,
    [purchase_order_id] NVARCHAR(64) NOT NULL,
    [supplier_id] NVARCHAR(64) NOT NULL,
    [invoice_date] DATE NOT NULL,
    [due_date] DATE NOT NULL,
    [invoice_amount_vnd] BIGINT NOT NULL,
    [payment_status] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_supplier_invoices] PRIMARY KEY ([supplier_invoice_id])
);
GO

CREATE TABLE silver.[supplier_payments] (
    [supplier_payment_id] NVARCHAR(64) NOT NULL,
    [supplier_invoice_id] NVARCHAR(64) NOT NULL,
    [supplier_id] NVARCHAR(64) NOT NULL,
    [payment_date] DATE NOT NULL,
    [paid_amount_vnd] BIGINT NOT NULL,
    [payment_method] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_supplier_payments] PRIMARY KEY ([supplier_payment_id])
);
GO


-- ======================================================================
-- F. AFTER-SALES / PROCESS / INTERVENTIONS
-- ======================================================================

CREATE TABLE silver.[after_sales_cases] (
    [case_id] NVARCHAR(64) NOT NULL,
    [sales_order_id] NVARCHAR(64) NOT NULL,
    [customer_id] NVARCHAR(64) NOT NULL,
    [opened_date] DATE NOT NULL,
    [closed_date] DATE NULL,
    [issue_type] NVARCHAR(100) NOT NULL,
    [severity] NVARCHAR(100) NOT NULL,
    [resolution_days] INT NULL,
    [csat_score] INT NULL,
    [repeat_order_within_90d_flag] BIT NOT NULL,
    [case_status] NVARCHAR(100) NOT NULL,
    CONSTRAINT [PK_after_sales_cases] PRIMARY KEY ([case_id])
);
GO

CREATE TABLE silver.[process_events] (
    [process_event_id] NVARCHAR(64) NOT NULL,
    [case_type] NVARCHAR(255) NOT NULL,
    [case_id] NVARCHAR(64) NOT NULL,
    [event_timestamp] DATETIME2(0) NOT NULL,
    [department] NVARCHAR(255) NOT NULL,
    [event_stage] NVARCHAR(255) NOT NULL,
    [event_description] NVARCHAR(1000) NOT NULL,
    [duration_from_previous_hours] DECIMAL(18,4) NULL,
    CONSTRAINT [PK_process_events] PRIMARY KEY ([process_event_id])
);
GO

CREATE TABLE silver.[business_interventions] (
    [intervention_id] NVARCHAR(64) NOT NULL,
    [effective_date] DATE NOT NULL,
    [project] NVARCHAR(255) NOT NULL,
    [intervention_name] NVARCHAR(255) NOT NULL,
    [description] NVARCHAR(1000) NOT NULL,
    [target_kpis] NVARCHAR(1000) NOT NULL,
    [measurement_note] NVARCHAR(1000) NOT NULL,
    CONSTRAINT [PK_business_interventions] PRIMARY KEY ([intervention_id])
);
GO


-- ======================================================================
-- G. FOREIGN KEY CONSTRAINTS
-- ======================================================================

ALTER TABLE silver.[employees] WITH CHECK
ADD CONSTRAINT [FK_employees_department_id__departments_department_id] FOREIGN KEY ([department_id])
REFERENCES silver.[departments] ([department_id]);
GO

ALTER TABLE silver.[product_supplier_map] WITH CHECK
ADD CONSTRAINT [FK_product_supplier_map_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[product_supplier_map] WITH CHECK
ADD CONSTRAINT [FK_product_supplier_map_supplier_id__suppliers_supplier_id] FOREIGN KEY ([supplier_id])
REFERENCES silver.[suppliers] ([supplier_id]);
GO

ALTER TABLE silver.[leads] WITH CHECK
ADD CONSTRAINT [FK_leads_customer_id__customers_customer_id] FOREIGN KEY ([customer_id])
REFERENCES silver.[customers] ([customer_id]);
GO

ALTER TABLE silver.[leads] WITH CHECK
ADD CONSTRAINT [FK_leads_campaign_id__marketing_campaigns_campaign_id] FOREIGN KEY ([campaign_id])
REFERENCES silver.[marketing_campaigns] ([campaign_id]);
GO

ALTER TABLE silver.[sales_activities] WITH CHECK
ADD CONSTRAINT [FK_sales_activities_employee_id__employees_employee_id] FOREIGN KEY ([employee_id])
REFERENCES silver.[employees] ([employee_id]);
GO

ALTER TABLE silver.[quotations] WITH CHECK
ADD CONSTRAINT [FK_quotations_customer_id__customers_customer_id] FOREIGN KEY ([customer_id])
REFERENCES silver.[customers] ([customer_id]);
GO

ALTER TABLE silver.[quotations] WITH CHECK
ADD CONSTRAINT [FK_quotations_salesperson_id__employees_employee_id] FOREIGN KEY ([salesperson_id])
REFERENCES silver.[employees] ([employee_id]);
GO

ALTER TABLE silver.[quotation_lines] WITH CHECK
ADD CONSTRAINT [FK_quotation_lines_quotation_id__quotations_quotation_id] FOREIGN KEY ([quotation_id])
REFERENCES silver.[quotations] ([quotation_id]);
GO

ALTER TABLE silver.[quotation_lines] WITH CHECK
ADD CONSTRAINT [FK_quotation_lines_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[sales_orders] WITH CHECK
ADD CONSTRAINT [FK_sales_orders_quotation_id__quotations_quotation_id] FOREIGN KEY ([quotation_id])
REFERENCES silver.[quotations] ([quotation_id]);
GO

ALTER TABLE silver.[sales_orders] WITH CHECK
ADD CONSTRAINT [FK_sales_orders_customer_id__customers_customer_id] FOREIGN KEY ([customer_id])
REFERENCES silver.[customers] ([customer_id]);
GO

ALTER TABLE silver.[sales_orders] WITH CHECK
ADD CONSTRAINT [FK_sales_orders_salesperson_id__employees_employee_id] FOREIGN KEY ([salesperson_id])
REFERENCES silver.[employees] ([employee_id]);
GO

ALTER TABLE silver.[sales_order_lines] WITH CHECK
ADD CONSTRAINT [FK_sales_order_lines_sales_order_id__sales_orders_sales_order_id] FOREIGN KEY ([sales_order_id])
REFERENCES silver.[sales_orders] ([sales_order_id]);
GO

ALTER TABLE silver.[sales_order_lines] WITH CHECK
ADD CONSTRAINT [FK_sales_order_lines_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[marketing_daily] WITH CHECK
ADD CONSTRAINT [FK_marketing_daily_campaign_id__marketing_campaigns_campaign_id] FOREIGN KEY ([campaign_id])
REFERENCES silver.[marketing_campaigns] ([campaign_id]);
GO

ALTER TABLE silver.[inventory_policy_history] WITH CHECK
ADD CONSTRAINT [FK_inventory_policy_history_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[inventory_daily_snapshot] WITH CHECK
ADD CONSTRAINT [FK_inventory_daily_snapshot_warehouse_id__warehouses_warehouse_id] FOREIGN KEY ([warehouse_id])
REFERENCES silver.[warehouses] ([warehouse_id]);
GO

ALTER TABLE silver.[inventory_daily_snapshot] WITH CHECK
ADD CONSTRAINT [FK_inventory_daily_snapshot_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[inventory_movements] WITH CHECK
ADD CONSTRAINT [FK_inventory_movements_warehouse_id__warehouses_warehouse_id] FOREIGN KEY ([warehouse_id])
REFERENCES silver.[warehouses] ([warehouse_id]);
GO

ALTER TABLE silver.[inventory_movements] WITH CHECK
ADD CONSTRAINT [FK_inventory_movements_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[purchase_orders] WITH CHECK
ADD CONSTRAINT [FK_purchase_orders_supplier_id__suppliers_supplier_id] FOREIGN KEY ([supplier_id])
REFERENCES silver.[suppliers] ([supplier_id]);
GO

ALTER TABLE silver.[purchase_orders] WITH CHECK
ADD CONSTRAINT [FK_purchase_orders_buyer_id__employees_employee_id] FOREIGN KEY ([buyer_id])
REFERENCES silver.[employees] ([employee_id]);
GO

ALTER TABLE silver.[purchase_order_lines] WITH CHECK
ADD CONSTRAINT [FK_purchase_order_lines_purchase_order_id__purchase_orders_purchase_order_id] FOREIGN KEY ([purchase_order_id])
REFERENCES silver.[purchase_orders] ([purchase_order_id]);
GO

ALTER TABLE silver.[purchase_order_lines] WITH CHECK
ADD CONSTRAINT [FK_purchase_order_lines_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[qa_events] WITH CHECK
ADD CONSTRAINT [FK_qa_events_purchase_order_id__purchase_orders_purchase_order_id] FOREIGN KEY ([purchase_order_id])
REFERENCES silver.[purchase_orders] ([purchase_order_id]);
GO

ALTER TABLE silver.[qa_events] WITH CHECK
ADD CONSTRAINT [FK_qa_events_purchase_order_line_id__purchase_order_lines_purchase_order_line_id] FOREIGN KEY ([purchase_order_line_id])
REFERENCES silver.[purchase_order_lines] ([purchase_order_line_id]);
GO

ALTER TABLE silver.[qa_events] WITH CHECK
ADD CONSTRAINT [FK_qa_events_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[qa_events] WITH CHECK
ADD CONSTRAINT [FK_qa_events_supplier_id__suppliers_supplier_id] FOREIGN KEY ([supplier_id])
REFERENCES silver.[suppliers] ([supplier_id]);
GO

ALTER TABLE silver.[shipments] WITH CHECK
ADD CONSTRAINT [FK_shipments_sales_order_id__sales_orders_sales_order_id] FOREIGN KEY ([sales_order_id])
REFERENCES silver.[sales_orders] ([sales_order_id]);
GO

ALTER TABLE silver.[shipments] WITH CHECK
ADD CONSTRAINT [FK_shipments_warehouse_id__warehouses_warehouse_id] FOREIGN KEY ([warehouse_id])
REFERENCES silver.[warehouses] ([warehouse_id]);
GO

ALTER TABLE silver.[shipment_lines] WITH CHECK
ADD CONSTRAINT [FK_shipment_lines_shipment_id__shipments_shipment_id] FOREIGN KEY ([shipment_id])
REFERENCES silver.[shipments] ([shipment_id]);
GO

ALTER TABLE silver.[shipment_lines] WITH CHECK
ADD CONSTRAINT [FK_shipment_lines_sales_order_line_id__sales_order_lines_sales_order_line_id] FOREIGN KEY ([sales_order_line_id])
REFERENCES silver.[sales_order_lines] ([sales_order_line_id]);
GO

ALTER TABLE silver.[shipment_lines] WITH CHECK
ADD CONSTRAINT [FK_shipment_lines_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[invoices] WITH CHECK
ADD CONSTRAINT [FK_invoices_sales_order_id__sales_orders_sales_order_id] FOREIGN KEY ([sales_order_id])
REFERENCES silver.[sales_orders] ([sales_order_id]);
GO

ALTER TABLE silver.[invoices] WITH CHECK
ADD CONSTRAINT [FK_invoices_customer_id__customers_customer_id] FOREIGN KEY ([customer_id])
REFERENCES silver.[customers] ([customer_id]);
GO

ALTER TABLE silver.[invoice_lines] WITH CHECK
ADD CONSTRAINT [FK_invoice_lines_invoice_id__invoices_invoice_id] FOREIGN KEY ([invoice_id])
REFERENCES silver.[invoices] ([invoice_id]);
GO

ALTER TABLE silver.[invoice_lines] WITH CHECK
ADD CONSTRAINT [FK_invoice_lines_sales_order_line_id__sales_order_lines_sales_order_line_id] FOREIGN KEY ([sales_order_line_id])
REFERENCES silver.[sales_order_lines] ([sales_order_line_id]);
GO

ALTER TABLE silver.[invoice_lines] WITH CHECK
ADD CONSTRAINT [FK_invoice_lines_product_id__products_product_id] FOREIGN KEY ([product_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[payments] WITH CHECK
ADD CONSTRAINT [FK_payments_invoice_id__invoices_invoice_id] FOREIGN KEY ([invoice_id])
REFERENCES silver.[invoices] ([invoice_id]);
GO

ALTER TABLE silver.[payments] WITH CHECK
ADD CONSTRAINT [FK_payments_customer_id__customers_customer_id] FOREIGN KEY ([customer_id])
REFERENCES silver.[customers] ([customer_id]);
GO

ALTER TABLE silver.[supplier_invoices] WITH CHECK
ADD CONSTRAINT [FK_supplier_invoices_purchase_order_id__purchase_orders_purchase_order_id] FOREIGN KEY ([purchase_order_id])
REFERENCES silver.[purchase_orders] ([purchase_order_id]);
GO

ALTER TABLE silver.[supplier_invoices] WITH CHECK
ADD CONSTRAINT [FK_supplier_invoices_supplier_id__suppliers_supplier_id] FOREIGN KEY ([supplier_id])
REFERENCES silver.[suppliers] ([supplier_id]);
GO

ALTER TABLE silver.[supplier_payments] WITH CHECK
ADD CONSTRAINT [FK_supplier_payments_supplier_invoice_id__supplier_invoices_supplier_invoice_id] FOREIGN KEY ([supplier_invoice_id])
REFERENCES silver.[supplier_invoices] ([supplier_invoice_id]);
GO

ALTER TABLE silver.[supplier_payments] WITH CHECK
ADD CONSTRAINT [FK_supplier_payments_supplier_id__suppliers_supplier_id] FOREIGN KEY ([supplier_id])
REFERENCES silver.[suppliers] ([supplier_id]);
GO

ALTER TABLE silver.[after_sales_cases] WITH CHECK
ADD CONSTRAINT [FK_after_sales_cases_sales_order_id__sales_orders_sales_order_id] FOREIGN KEY ([sales_order_id])
REFERENCES silver.[sales_orders] ([sales_order_id]);
GO

ALTER TABLE silver.[after_sales_cases] WITH CHECK
ADD CONSTRAINT [FK_after_sales_cases_customer_id__customers_customer_id] FOREIGN KEY ([customer_id])
REFERENCES silver.[customers] ([customer_id]);
GO

ALTER TABLE silver.[leads] WITH CHECK
ADD CONSTRAINT [FK_leads_assigned_sales_id__employees_employee_id] FOREIGN KEY ([assigned_sales_id])
REFERENCES silver.[employees] ([employee_id]);
GO

ALTER TABLE silver.[leads] WITH CHECK
ADD CONSTRAINT [FK_leads_product_interest_id__products_product_id] FOREIGN KEY ([product_interest_id])
REFERENCES silver.[products] ([product_id]);
GO

ALTER TABLE silver.[sales_activities] WITH CHECK
ADD CONSTRAINT [FK_sales_activities_lead_id__leads_lead_id] FOREIGN KEY ([lead_id])
REFERENCES silver.[leads] ([lead_id]);
GO

ALTER TABLE silver.[quotations] WITH CHECK
ADD CONSTRAINT [FK_quotations_lead_id__leads_lead_id] FOREIGN KEY ([lead_id])
REFERENCES silver.[leads] ([lead_id]);
GO

ALTER TABLE silver.[sales_orders] WITH CHECK
ADD CONSTRAINT [FK_sales_orders_lead_id__leads_lead_id] FOREIGN KEY ([lead_id])
REFERENCES silver.[leads] ([lead_id]);
GO

-- ======================================================================
-- H. FK INDEXES (for joins / Power BI / validation)
-- ======================================================================

CREATE INDEX [IX_employees_department_id] ON silver.[employees] ([department_id]);
GO

CREATE INDEX [IX_product_supplier_map_product_id] ON silver.[product_supplier_map] ([product_id]);
GO

CREATE INDEX [IX_product_supplier_map_supplier_id] ON silver.[product_supplier_map] ([supplier_id]);
GO

CREATE INDEX [IX_leads_customer_id] ON silver.[leads] ([customer_id]);
GO

CREATE INDEX [IX_leads_campaign_id] ON silver.[leads] ([campaign_id]);
GO

CREATE INDEX [IX_sales_activities_employee_id] ON silver.[sales_activities] ([employee_id]);
GO

CREATE INDEX [IX_quotations_customer_id] ON silver.[quotations] ([customer_id]);
GO

CREATE INDEX [IX_quotations_salesperson_id] ON silver.[quotations] ([salesperson_id]);
GO

CREATE INDEX [IX_quotation_lines_quotation_id] ON silver.[quotation_lines] ([quotation_id]);
GO

CREATE INDEX [IX_quotation_lines_product_id] ON silver.[quotation_lines] ([product_id]);
GO

CREATE INDEX [IX_sales_orders_quotation_id] ON silver.[sales_orders] ([quotation_id]);
GO

CREATE INDEX [IX_sales_orders_customer_id] ON silver.[sales_orders] ([customer_id]);
GO

CREATE INDEX [IX_sales_orders_salesperson_id] ON silver.[sales_orders] ([salesperson_id]);
GO

CREATE INDEX [IX_sales_order_lines_sales_order_id] ON silver.[sales_order_lines] ([sales_order_id]);
GO

CREATE INDEX [IX_sales_order_lines_product_id] ON silver.[sales_order_lines] ([product_id]);
GO

CREATE INDEX [IX_marketing_daily_campaign_id] ON silver.[marketing_daily] ([campaign_id]);
GO

CREATE INDEX [IX_inventory_policy_history_product_id] ON silver.[inventory_policy_history] ([product_id]);
GO

CREATE INDEX [IX_inventory_daily_snapshot_warehouse_id] ON silver.[inventory_daily_snapshot] ([warehouse_id]);
GO

CREATE INDEX [IX_inventory_daily_snapshot_product_id] ON silver.[inventory_daily_snapshot] ([product_id]);
GO

CREATE INDEX [IX_inventory_movements_warehouse_id] ON silver.[inventory_movements] ([warehouse_id]);
GO

CREATE INDEX [IX_inventory_movements_product_id] ON silver.[inventory_movements] ([product_id]);
GO

CREATE INDEX [IX_purchase_orders_supplier_id] ON silver.[purchase_orders] ([supplier_id]);
GO

CREATE INDEX [IX_purchase_orders_buyer_id] ON silver.[purchase_orders] ([buyer_id]);
GO

CREATE INDEX [IX_purchase_order_lines_purchase_order_id] ON silver.[purchase_order_lines] ([purchase_order_id]);
GO

CREATE INDEX [IX_purchase_order_lines_product_id] ON silver.[purchase_order_lines] ([product_id]);
GO

CREATE INDEX [IX_qa_events_purchase_order_id] ON silver.[qa_events] ([purchase_order_id]);
GO

CREATE INDEX [IX_qa_events_purchase_order_line_id] ON silver.[qa_events] ([purchase_order_line_id]);
GO

CREATE INDEX [IX_qa_events_product_id] ON silver.[qa_events] ([product_id]);
GO

CREATE INDEX [IX_qa_events_supplier_id] ON silver.[qa_events] ([supplier_id]);
GO

CREATE INDEX [IX_shipments_sales_order_id] ON silver.[shipments] ([sales_order_id]);
GO

CREATE INDEX [IX_shipments_warehouse_id] ON silver.[shipments] ([warehouse_id]);
GO

CREATE INDEX [IX_shipment_lines_shipment_id] ON silver.[shipment_lines] ([shipment_id]);
GO

CREATE INDEX [IX_shipment_lines_sales_order_line_id] ON silver.[shipment_lines] ([sales_order_line_id]);
GO

CREATE INDEX [IX_shipment_lines_product_id] ON silver.[shipment_lines] ([product_id]);
GO

CREATE INDEX [IX_invoices_sales_order_id] ON silver.[invoices] ([sales_order_id]);
GO

CREATE INDEX [IX_invoices_customer_id] ON silver.[invoices] ([customer_id]);
GO

CREATE INDEX [IX_invoice_lines_invoice_id] ON silver.[invoice_lines] ([invoice_id]);
GO

CREATE INDEX [IX_invoice_lines_sales_order_line_id] ON silver.[invoice_lines] ([sales_order_line_id]);
GO

CREATE INDEX [IX_invoice_lines_product_id] ON silver.[invoice_lines] ([product_id]);
GO

CREATE INDEX [IX_payments_invoice_id] ON silver.[payments] ([invoice_id]);
GO

CREATE INDEX [IX_payments_customer_id] ON silver.[payments] ([customer_id]);
GO

CREATE INDEX [IX_supplier_invoices_purchase_order_id] ON silver.[supplier_invoices] ([purchase_order_id]);
GO

CREATE INDEX [IX_supplier_invoices_supplier_id] ON silver.[supplier_invoices] ([supplier_id]);
GO

CREATE INDEX [IX_supplier_payments_supplier_invoice_id] ON silver.[supplier_payments] ([supplier_invoice_id]);
GO

CREATE INDEX [IX_supplier_payments_supplier_id] ON silver.[supplier_payments] ([supplier_id]);
GO

CREATE INDEX [IX_after_sales_cases_sales_order_id] ON silver.[after_sales_cases] ([sales_order_id]);
GO

CREATE INDEX [IX_after_sales_cases_customer_id] ON silver.[after_sales_cases] ([customer_id]);
GO

CREATE INDEX [IX_leads_assigned_sales_id] ON silver.[leads] ([assigned_sales_id]);
GO

CREATE INDEX [IX_leads_product_interest_id] ON silver.[leads] ([product_interest_id]);
GO

CREATE INDEX [IX_sales_activities_lead_id] ON silver.[sales_activities] ([lead_id]);
GO

CREATE INDEX [IX_quotations_lead_id] ON silver.[quotations] ([lead_id]);
GO

CREATE INDEX [IX_sales_orders_lead_id] ON silver.[sales_orders] ([lead_id]);
GO

-- ======================================================================
-- I. DATE STANDARD / USAGE EXAMPLES
-- ======================================================================

-- Input dd/mm/yyyy text during Silver cleaning:
-- SELECT TRY_CONVERT(DATE, N'14/09/2026', 103) AS parsed_date;

-- Output dd/mm/yyyy for reporting/export:
-- SELECT CONVERT(CHAR(10), order_date, 103) AS order_date_ddmmyyyy
-- FROM silver.sales_orders;

-- IMPORTANT: Keep DATE/DATETIME2 in Silver. Do not store dates as NVARCHAR
-- merely to force a visual dd/mm/yyyy format.

-- process_events special relationship:
--   PK = process_event_id
--   logical process key = (case_type, case_id)
--   case_id is polymorphic, therefore intentionally has no single DB-level FK.

-- ======================================================================
-- END OF SILVER DDL
-- ======================================================================
