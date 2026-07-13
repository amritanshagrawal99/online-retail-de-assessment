-- =====================================================================
-- 02_load_transform.sql  —  Transform & model
-- Dialect: BigQuery Standard SQL
-- =====================================================================
-- Builds, idempotently (CREATE OR REPLACE ... AS SELECT), from raw:
--   stg_online_retail  ->  dim_customer, dim_product  ->  fct_sales, fct_orders
--
-- Every model is modular (CTEs, no 200-line monoliths). Read top-to-bottom.
--
-- Service-line detection: a "real" product code begins with 5 digits
-- (e.g. 85123A). Service/non-product codes (POST, DOT, M, BANK CHARGES,
-- AMAZONFEE, gift_0001_*, TEST001, ...) do not. We test this WITHOUT regex so
-- the exact same SQL runs on BigQuery and (for local verification) DuckDB:
--     SAFE_CAST(SUBSTR(stock_code, 1, 5) AS INT64) IS NULL  ==> service line
-- =====================================================================


-- ---------------------------------------------------------------------
-- STAGING — type, rename, flag, de-duplicate (line grain preserved)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE `online_retail.stg_online_retail` AS
WITH source AS (
  SELECT * FROM `online_retail.raw_online_retail`
),

-- The source contains 12,133 exact-duplicate lines. Rank rows sharing a
-- natural-line hash and keep exactly one. _record_hash is computed at ingest
-- from (invoice, stock_code, quantity, invoice_date, price, customer_id).
deduped AS (
  SELECT *
  FROM source
  QUALIFY ROW_NUMBER() OVER (
            PARTITION BY _record_hash
            ORDER BY _source_sheet, _source_row
          ) = 1
),

typed AS (
  SELECT
    _record_hash                                            AS line_sk,
    invoice                                                 AS invoice_no,
    stock_code,
    COALESCE(NULLIF(TRIM(description), ''), 'UNKNOWN')      AS description,
    quantity,
    CAST(price AS NUMERIC)                                  AS unit_price,
    CAST(quantity * price AS NUMERIC)                       AS line_revenue,
    invoice_date                                            AS invoice_ts,
    DATE(invoice_date)                                      AS invoice_date,
    SAFE_CAST(customer_id AS INT64)                         AS customer_id,
    COALESCE(NULLIF(TRIM(country), ''), 'Unspecified')      AS country,
    -- data-quality flags (computed once, reused everywhere downstream)
    STARTS_WITH(invoice, 'C')                               AS is_cancellation,
    (quantity <= 0)                                         AS is_return_or_zero,
    (price <= 0)                                            AS is_nonpositive_price,
    (SAFE_CAST(SUBSTR(stock_code, 1, 5) AS INT64) IS NULL)  AS is_service_line,
    (customer_id IS NOT NULL)                               AS has_customer,
    _source_file                                           AS source_file,
    _loaded_at                                             AS loaded_at
  FROM deduped
),

flagged AS (
  SELECT
    *,
    -- A row is a "valid sale" for product analytics iff it is a positive,
    -- attributable purchase of a real product (not a return/cancel/fee/guest).
    (NOT is_cancellation
     AND NOT is_return_or_zero
     AND NOT is_nonpositive_price
     AND NOT is_service_line
     AND has_customer)                                      AS is_valid_sale
  FROM typed
)

SELECT
  line_sk, invoice_no, stock_code, description, quantity, unit_price, line_revenue,
  invoice_ts, invoice_date, customer_id, country,
  is_cancellation, is_return_or_zero, is_nonpositive_price, is_service_line,
  has_customer, is_valid_sale, source_file, loaded_at
FROM flagged;


-- ---------------------------------------------------------------------
-- DIM_CUSTOMER — one row per attributable customer (guests excluded).
-- Identity covers EVERY known customer_id (so fct_sales FKs never orphan);
-- value metrics are measured over PRODUCT lines only, returns netted out.
-- A customer seen only on service lines (postage/fees) still appears here,
-- with 0 revenue and NULL purchase dates.
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE `online_retail.dim_customer` AS
WITH customer_lines AS (
  SELECT * FROM `online_retail.stg_online_retail` WHERE has_customer
),
ids AS (
  SELECT DISTINCT customer_id FROM customer_lines
),

-- Modal country per customer (a few customers ship to >1 country).
country_counts AS (
  SELECT customer_id, country, COUNT(*) AS n
  FROM customer_lines
  GROUP BY customer_id, country
),
primary_country AS (
  SELECT customer_id, country AS primary_country
  FROM country_counts
  QUALIFY ROW_NUMBER() OVER (
            PARTITION BY customer_id
            ORDER BY n DESC, country
          ) = 1
),

-- Value is measured on real product purchases only (postage/fees excluded).
product_lines AS (
  SELECT * FROM customer_lines WHERE NOT is_service_line
),
value_agg AS (
  SELECT
    customer_id,
    MIN(invoice_date)          AS first_purchase_date,
    MAX(invoice_date)          AS last_purchase_date,
    COUNT(DISTINCT invoice_no) AS lifetime_orders,
    SUM(line_revenue)          AS lifetime_net_revenue   -- returns reduce this
  FROM product_lines
  GROUP BY customer_id
)

SELECT
  TO_HEX(MD5(CAST(ids.customer_id AS STRING)))  AS customer_sk,
  ids.customer_id,
  pc.primary_country,
  v.first_purchase_date,
  v.last_purchase_date,
  COALESCE(v.lifetime_orders, 0)                AS lifetime_orders,
  COALESCE(v.lifetime_net_revenue, 0)           AS lifetime_net_revenue
FROM ids
JOIN primary_country pc USING (customer_id)
LEFT JOIN value_agg v    USING (customer_id);


-- ---------------------------------------------------------------------
-- DIM_PRODUCT — one row per distinct stock_code (products + service codes)
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE `online_retail.dim_product` AS
WITH base AS (
  SELECT * FROM `online_retail.stg_online_retail`
),

-- Canonical description = the most frequently used non-UNKNOWN text.
desc_counts AS (
  SELECT stock_code, description, COUNT(*) AS n
  FROM base
  WHERE description <> 'UNKNOWN'
  GROUP BY stock_code, description
),
canonical_desc AS (
  SELECT stock_code, description
  FROM desc_counts
  QUALIFY ROW_NUMBER() OVER (
            PARTITION BY stock_code
            ORDER BY n DESC, description
          ) = 1
),

agg AS (
  SELECT
    stock_code,
    MAX(is_service_line) AS is_service_line,
    MIN(invoice_date)    AS first_seen_date,
    MAX(invoice_date)    AS last_seen_date
  FROM base
  GROUP BY stock_code
)

SELECT
  TO_HEX(MD5(a.stock_code))                      AS product_sk,
  a.stock_code,
  COALESCE(cd.description, 'UNKNOWN')             AS description,
  a.is_service_line,
  a.first_seen_date,
  a.last_seen_date
FROM agg a
LEFT JOIN canonical_desc cd USING (stock_code);


-- ---------------------------------------------------------------------
-- FCT_SALES — line grain. Keeps ALL lines, flagged, with FKs to dims.
-- Analysts filter (is_valid_sale / is_service_line) for the question at hand.
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE `online_retail.fct_sales` AS
SELECT
  s.line_sk,
  s.invoice_no,
  CASE WHEN s.has_customer
       THEN TO_HEX(MD5(CAST(s.customer_id AS STRING)))
       ELSE NULL END               AS customer_sk,   -- FK -> dim_customer
  TO_HEX(MD5(s.stock_code))        AS product_sk,     -- FK -> dim_product
  s.invoice_date,
  s.invoice_ts,
  s.quantity,
  s.unit_price,
  s.line_revenue,
  s.is_cancellation,
  s.is_service_line,
  s.has_customer
FROM `online_retail.stg_online_retail` s;


-- ---------------------------------------------------------------------
-- FCT_ORDERS — invoice grain (bonus). One row per invoice.
-- ---------------------------------------------------------------------
CREATE OR REPLACE TABLE `online_retail.fct_orders` AS
SELECT
  invoice_no,
  MAX(CASE WHEN has_customer
           THEN TO_HEX(MD5(CAST(customer_id AS STRING)))
           ELSE NULL END)          AS customer_sk,
  MIN(invoice_date)                AS invoice_date,
  MIN(invoice_ts)                  AS invoice_ts,
  ANY_VALUE(country)               AS country,
  COUNT(*)                         AS n_lines,
  SUM(quantity)                    AS n_units,
  SUM(line_revenue)                AS order_revenue,
  MAX(is_cancellation)             AS is_cancellation
FROM `online_retail.stg_online_retail`
GROUP BY invoice_no;
