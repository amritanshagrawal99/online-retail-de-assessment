-- =====================================================================
-- 03_analytics_queries.sql  —  Analytics SQL
-- Dialect: BigQuery Standard SQL
-- =====================================================================
-- Conventions used throughout:
--   * "net product revenue" = SUM(line_revenue) over NON-service lines, with
--     returns/cancellations left in as negatives (so it is true net revenue).
--   * Service codes (POST/DOT/M/fees) are excluded from product analytics —
--     DOT alone is ~£322k of postage that would otherwise masquerade as sales.
-- Each query is independently runnable and answers one required question.
-- =====================================================================


-- ---------------------------------------------------------------------
-- Q1. Top 10 customers by net lifetime revenue
--     (the dimension already pre-aggregates this cleanly)
-- ---------------------------------------------------------------------
SELECT
  customer_id,
  primary_country,
  lifetime_orders,
  ROUND(lifetime_net_revenue, 2) AS net_revenue_gbp
FROM `online_retail.dim_customer`
ORDER BY lifetime_net_revenue DESC
LIMIT 10;


-- ---------------------------------------------------------------------
-- Q2. Monthly net product revenue trend
-- ---------------------------------------------------------------------
SELECT
  FORMAT_DATE('%Y-%m', invoice_date)      AS month,
  COUNT(DISTINCT invoice_no)              AS orders,
  COUNT(DISTINCT customer_sk)             AS active_customers,
  ROUND(SUM(line_revenue), 2)            AS net_revenue_gbp
FROM `online_retail.fct_sales`
WHERE NOT is_service_line
GROUP BY month
ORDER BY month;


-- ---------------------------------------------------------------------
-- Q3. Outlier / anomaly detection — orders whose value is a statistical
--     outlier (|z-score| > 3 against the mean order value).
--     z = (order_revenue - mean) / stddev, computed over positive orders.
-- ---------------------------------------------------------------------
WITH orders AS (
  SELECT invoice_no, customer_sk, invoice_date, order_revenue
  FROM `online_retail.fct_orders`
  WHERE NOT is_cancellation
    AND order_revenue > 0
),
stats AS (
  SELECT AVG(order_revenue) AS mu, STDDEV_SAMP(order_revenue) AS sigma
  FROM orders
)
SELECT
  o.invoice_no,
  o.customer_sk,
  o.invoice_date,
  ROUND(o.order_revenue, 2)                       AS order_revenue_gbp,
  ROUND((o.order_revenue - s.mu) / s.sigma, 2)    AS z_score
FROM orders o
CROSS JOIN stats s
WHERE (o.order_revenue - s.mu) / s.sigma > 3      -- unusually LARGE orders
ORDER BY z_score DESC
LIMIT 20;


-- ---------------------------------------------------------------------
-- Q4. Geographic breakdown — net revenue by ship-to country
-- ---------------------------------------------------------------------
SELECT
  country,
  COUNT(DISTINCT invoice_no)                              AS orders,
  COUNT(DISTINCT customer_sk)                             AS customers,
  ROUND(SUM(order_revenue), 2)                            AS net_revenue_gbp,
  ROUND(100 * SUM(order_revenue)
            / SUM(SUM(order_revenue)) OVER (), 2)         AS pct_of_total
FROM `online_retail.fct_orders`
WHERE NOT is_cancellation
GROUP BY country
ORDER BY net_revenue_gbp DESC
LIMIT 15;


-- ---------------------------------------------------------------------
-- Q5. Stakeholder-on-short-notice: "What were our best-selling products
--     this year, and how concentrated is revenue in the top few?"
--     (:start_date is a parameter — 2011-01-01 = the latest full year.)
-- ---------------------------------------------------------------------
WITH recent AS (
  SELECT f.product_sk, p.description, f.line_revenue
  FROM `online_retail.fct_sales` f
  JOIN `online_retail.dim_product` p USING (product_sk)
  WHERE NOT f.is_service_line
    AND f.invoice_date >= DATE '2011-01-01'
)
SELECT
  description,
  ROUND(SUM(line_revenue), 2)                          AS net_revenue_gbp,
  ROUND(100 * SUM(line_revenue)
            / SUM(SUM(line_revenue)) OVER (), 2)       AS pct_of_period_revenue
FROM recent
GROUP BY description
ORDER BY net_revenue_gbp DESC
LIMIT 10;
