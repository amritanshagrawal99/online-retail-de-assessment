-- =====================================================================
-- 01_create_tables.sql  —  Warehouse design
-- Dialect: BigQuery Standard SQL
-- =====================================================================
-- Layered design (3 layers, one dataset per environment):
--
--   RAW      raw_online_retail   faithful landing, 1:1 with the source file
--   STAGING  stg_online_retail   typed, renamed, flagged, de-duplicated
--   MARTS    dim_customer        conformed customer dimension  (surrogate key)
--            dim_product         conformed product dimension   (surrogate key)
--            fct_sales           line-grain sales fact          (FK to dims)
--            fct_orders          invoice-grain sales fact       (bonus)
--
-- Key strategy
--   * Surrogate keys are deterministic hashes of the natural key:
--       customer_sk = TO_HEX(MD5(CAST(customer_id AS STRING)))
--       product_sk  = TO_HEX(MD5(stock_code))
--     Deterministic hashing (vs a random UUID / monotonic id) means keys are
--     STABLE across full reloads, so the fact table's FKs never break when the
--     pipeline is re-run — critical for an idempotent warehouse.
--   * Duplicates: the source has 12,133 exact-duplicate lines. They are removed
--     in staging by ranking rows within their natural-line hash (_record_hash)
--     and keeping one. See 02_load_transform.sql.
--
-- Partitioning / clustering (cost + performance)
--   * Facts are partitioned by invoice_date (day) so time-bounded queries
--     (almost all analytics here) scan only the needed partitions.
--   * Clustering on the most-filtered keys keeps point-lookups cheap.
--
-- NOTE: 02_load_transform.sql uses CREATE OR REPLACE TABLE ... AS SELECT, which
-- (re)builds these tables idempotently. This file is the explicit schema
-- CONTRACT — run it first if you want the raw landing table + partitioning to
-- exist before loading, or read it as the documented design.
-- =====================================================================

CREATE SCHEMA IF NOT EXISTS `online_retail`
  OPTIONS (location = 'EU', description = 'Online Retail II warehouse');

-- ---------------------------------------------------------------------
-- RAW  —  populated by extract/load.py (WRITE_TRUNCATE, idempotent)
-- Faithful copy of the source; STRING where the source is ambiguous.
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `online_retail.raw_online_retail`
(
  invoice       STRING    OPTIONS (description = 'Invoice no.; leading C = cancellation'),
  stock_code    STRING    OPTIONS (description = 'Product code OR service code (POST/DOT/M/...)'),
  description   STRING    OPTIONS (description = 'Free-text product description; nullable'),
  quantity      INT64     OPTIONS (description = 'Units on the line; negative on returns'),
  invoice_date  TIMESTAMP OPTIONS (description = 'Line timestamp (store-local)'),
  price         FLOAT64   OPTIONS (description = 'Unit price in GBP; can be 0/negative'),
  customer_id   STRING    OPTIONS (description = 'Customer id as string; NULL = guest'),
  country       STRING    OPTIONS (description = 'Ship-to country'),
  _source_file  STRING    OPTIONS (description = 'Lineage: source filename'),
  _source_sheet STRING    OPTIONS (description = 'Lineage: source worksheet'),
  _source_row   INT64     OPTIONS (description = 'Lineage: 1-based source row'),
  _loaded_at    TIMESTAMP OPTIONS (description = 'Lineage: ingestion time (UTC)'),
  _record_hash  STRING    OPTIONS (description = 'MD5 of the natural line; dedup key')
)
PARTITION BY DATE(invoice_date)
CLUSTER BY invoice, stock_code
OPTIONS (description = 'RAW landing table for Online Retail II. 1:1 with source, no cleaning.');

-- ---------------------------------------------------------------------
-- STAGING  —  typed + renamed + flagged + de-duplicated line grain
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `online_retail.stg_online_retail`
(
  line_sk               STRING    OPTIONS (description = 'PK: hash of the natural line'),
  invoice_no            STRING,
  stock_code            STRING,
  description           STRING,
  quantity              INT64,
  unit_price            NUMERIC   OPTIONS (description = 'Money as NUMERIC to avoid float drift'),
  line_revenue          NUMERIC   OPTIONS (description = 'quantity * unit_price'),
  invoice_ts            TIMESTAMP,
  invoice_date          DATE,
  customer_id           INT64,
  country               STRING,
  is_cancellation       BOOL      OPTIONS (description = 'Invoice starts with C'),
  is_return_or_zero     BOOL      OPTIONS (description = 'quantity <= 0'),
  is_nonpositive_price  BOOL      OPTIONS (description = 'unit_price <= 0'),
  is_service_line       BOOL      OPTIONS (description = 'Non-product code: POST/DOT/M/...'),
  has_customer          BOOL      OPTIONS (description = 'customer_id is present'),
  is_valid_sale         BOOL      OPTIONS (description = 'Positive product sale, attributable'),
  source_file           STRING,
  loaded_at             TIMESTAMP
)
PARTITION BY invoice_date
CLUSTER BY customer_id, stock_code
OPTIONS (description = 'Cleaned, typed, flagged line grain. One row per de-duplicated source line.');

-- ---------------------------------------------------------------------
-- MARTS
-- ---------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS `online_retail.dim_customer`
(
  customer_sk        STRING  OPTIONS (description = 'PK: TO_HEX(MD5(customer_id))'),
  customer_id        INT64   OPTIONS (description = 'Natural key'),
  primary_country    STRING  OPTIONS (description = 'Modal country across the customer''s lines'),
  first_purchase_date DATE,
  last_purchase_date  DATE,
  lifetime_orders    INT64,
  lifetime_net_revenue NUMERIC
)
CLUSTER BY customer_sk
OPTIONS (description = 'One row per attributable customer (guests excluded).');

CREATE TABLE IF NOT EXISTS `online_retail.dim_product`
(
  product_sk        STRING  OPTIONS (description = 'PK: TO_HEX(MD5(stock_code))'),
  stock_code        STRING  OPTIONS (description = 'Natural key'),
  description       STRING  OPTIONS (description = 'Canonical (modal) description'),
  is_service_line   BOOL    OPTIONS (description = 'Non-product line (postage/fees/etc.)'),
  first_seen_date   DATE,
  last_seen_date    DATE
)
CLUSTER BY product_sk
OPTIONS (description = 'One row per distinct stock_code (products + service codes, flagged).');

CREATE TABLE IF NOT EXISTS `online_retail.fct_sales`
(
  line_sk         STRING    OPTIONS (description = 'Degenerate key -> stg line'),
  invoice_no      STRING,
  customer_sk     STRING    OPTIONS (description = 'FK -> dim_customer (NULL for guests)'),
  product_sk      STRING    OPTIONS (description = 'FK -> dim_product'),
  invoice_date    DATE,
  invoice_ts      TIMESTAMP,
  quantity        INT64,
  unit_price      NUMERIC,
  line_revenue    NUMERIC,
  is_cancellation BOOL,
  is_service_line BOOL,
  has_customer    BOOL
)
PARTITION BY invoice_date
CLUSTER BY customer_sk, product_sk
OPTIONS (description = 'Line-grain sales fact. Grain = one de-duplicated invoice line.');

CREATE TABLE IF NOT EXISTS `online_retail.fct_orders`
(
  invoice_no      STRING    OPTIONS (description = 'PK: one row per invoice'),
  customer_sk     STRING,
  invoice_date    DATE,
  invoice_ts      TIMESTAMP,
  country         STRING,
  n_lines         INT64,
  n_units         INT64,
  order_revenue   NUMERIC,
  is_cancellation BOOL
)
PARTITION BY invoice_date
CLUSTER BY customer_sk
OPTIONS (description = 'Invoice-grain fact (bonus). Grain = one invoice.');
