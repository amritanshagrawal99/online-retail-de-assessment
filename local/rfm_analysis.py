"""
local/rfm_analysis.py
=====================
Compute RFM segmentation + supporting figures from the built warehouse, and
write real numbers to local/analysis_outputs.json for use in the write-up.

Run AFTER build_local.py (it reads local/warehouse.duckdb).

RFM method
----------
Reference date = MAX(invoice_date) + 1 day (the "as-of" of the dataset).
For every customer with >=1 real product purchase:
  Recency   = days since last purchase          (lower = better)
  Frequency = distinct purchase invoices         (higher = better)
  Monetary  = net product revenue                (higher = better)
Score each into quintiles 1..5 (NTILE). Recency is reversed (recent -> 5).
Segments are the standard RFM map (Champions, Loyal, At Risk, etc.).
"""

from __future__ import annotations

import json
import sys
import io
from pathlib import Path

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / "local" / "warehouse.duckdb"
OUT = ROOT / "local" / "analysis_outputs.json"

# Standard RFM -> segment mapping, expressed as ranges on R and F (1..5).
SEGMENT_SQL = """
CREATE OR REPLACE TABLE online_retail.rfm AS
WITH ref AS (
  SELECT DATE (MAX(last_purchase_date) + INTERVAL 1 DAY) AS as_of
  FROM online_retail.dim_customer
),
base AS (
  SELECT
    customer_sk,
    customer_id,
    primary_country,
    DATE_DIFF('day', last_purchase_date, (SELECT as_of FROM ref)) AS recency_days,
    lifetime_orders                                               AS frequency,
    lifetime_net_revenue                                          AS monetary
  FROM online_retail.dim_customer
  WHERE lifetime_orders > 0 AND lifetime_net_revenue > 0
),
scored AS (
  SELECT *,
    -- customer_sk is a deterministic tiebreaker so quintile boundaries are
    -- stable across runs (NTILE otherwise splits ties in engine order).
    6 - NTILE(5) OVER (ORDER BY recency_days, customer_sk)  AS r,   -- recent -> 5
    NTILE(5) OVER (ORDER BY frequency, customer_sk)         AS f,
    NTILE(5) OVER (ORDER BY monetary, customer_sk)          AS m
  FROM base
)
SELECT *,
  CASE
    WHEN r >= 4 AND f >= 4 THEN 'Champions'
    WHEN r >= 3 AND f >= 3 THEN 'Loyal'
    WHEN r >= 4 AND f <= 2 THEN 'New / Promising'
    WHEN r = 3 AND f <= 2 THEN 'Potential Loyalist'
    WHEN r = 2 AND f >= 3 THEN 'At Risk'
    WHEN r = 2 AND f <= 2 THEN 'Hibernating'
    WHEN r = 1 AND f >= 4 THEN 'Cannot Lose Them'
    WHEN r = 1 AND f >= 3 THEN 'At Risk'
    ELSE 'Lost'
  END AS segment
FROM scored
"""


def main() -> int:
    if not DB_PATH.exists():
        print("Run local/build_local.py first."); return 1
    con = duckdb.connect(str(DB_PATH))
    con.execute(SEGMENT_SQL)

    out: dict = {}

    as_of = con.sql("SELECT MAX(last_purchase_date) FROM online_retail.dim_customer").fetchone()[0]
    out["as_of_last_purchase"] = str(as_of)

    # Segment table: size, revenue, avg recency/frequency/monetary
    seg = con.sql("""
      SELECT segment,
             COUNT(*)                             AS customers,
             ROUND(AVG(recency_days))             AS avg_recency_days,
             ROUND(AVG(frequency), 1)             AS avg_orders,
             ROUND(AVG(monetary), 0)              AS avg_net_revenue,
             ROUND(SUM(monetary), 0)              AS total_net_revenue,
             ROUND(100 * SUM(monetary) / SUM(SUM(monetary)) OVER (), 1) AS pct_revenue
      FROM online_retail.rfm
      GROUP BY segment
      ORDER BY total_net_revenue DESC
    """).df()
    print("=== RFM SEGMENTS ===")
    print(seg.to_string(index=False))
    out["segments"] = seg.to_dict(orient="records")

    # Headline totals
    totals = con.sql("""
      SELECT
        (SELECT COUNT(*) FROM online_retail.dim_customer)                      AS known_customers,
        (SELECT COUNT(*) FROM online_retail.rfm)                               AS purchasing_customers,
        (SELECT ROUND(SUM(line_revenue),0) FROM online_retail.fct_sales
           WHERE NOT is_service_line)                                          AS net_product_revenue,
        (SELECT ROUND(SUM(line_revenue),0) FROM online_retail.fct_sales
           WHERE NOT is_service_line AND has_customer)                         AS attributable_revenue,
        (SELECT ROUND(SUM(line_revenue),0) FROM online_retail.fct_sales
           WHERE NOT is_service_line AND NOT has_customer)                     AS guest_revenue
    """).df().iloc[0].to_dict()
    out["totals"] = {k: float(v) for k, v in totals.items()}
    print("\n=== TOTALS ===")
    for k, v in totals.items():
        print(f"  {k:24} {v:,.0f}")

    # Repeat-purchase behaviour
    repeat = con.sql("""
      WITH c AS (SELECT lifetime_orders FROM online_retail.dim_customer WHERE lifetime_orders > 0)
      SELECT
        COUNT(*)                                                   AS buyers,
        SUM(CASE WHEN lifetime_orders >= 2 THEN 1 ELSE 0 END)      AS repeat_buyers,
        ROUND(100.0 * SUM(CASE WHEN lifetime_orders >= 2 THEN 1 ELSE 0 END) / COUNT(*), 1) AS repeat_rate_pct
      FROM c
    """).df().iloc[0].to_dict()
    out["repeat"] = {k: float(v) for k, v in repeat.items()}
    print("\n=== REPEAT PURCHASE ===")
    for k, v in repeat.items():
        print(f"  {k:20} {v:,.1f}" if isinstance(v, float) else f"  {k:20} {v}")

    # Revenue concentration (Pareto): share from top 20% of customers
    pareto = con.sql("""
      WITH r AS (
        SELECT monetary,
               NTILE(5) OVER (ORDER BY monetary DESC) AS quintile
        FROM online_retail.rfm
      )
      SELECT
        ROUND(100 * SUM(CASE WHEN quintile = 1 THEN monetary ELSE 0 END) / SUM(monetary), 1)
          AS top20pct_revenue_share
      FROM r
    """).fetchone()[0]
    out["top20pct_revenue_share"] = float(pareto)
    print(f"\nTop 20% of customers drive {pareto}% of net revenue")

    # "At Risk" + "Cannot Lose Them" = the win-back target for the recommendation
    target = con.sql("""
      SELECT COUNT(*) AS customers,
             ROUND(AVG(monetary),0) AS avg_net_revenue,
             ROUND(SUM(monetary),0) AS total_hist_revenue,
             ROUND(AVG(recency_days)) AS avg_recency_days,
             ROUND(AVG(frequency),1)  AS avg_orders
      FROM online_retail.rfm
      WHERE segment IN ('At Risk','Cannot Lose Them')
    """).df().iloc[0].to_dict()
    out["winback_target"] = {k: float(v) for k, v in target.items()}
    print("\n=== WIN-BACK TARGET (At Risk + Cannot Lose Them) ===")
    for k, v in target.items():
        print(f"  {k:20} {v:,.1f}")

    OUT.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {OUT}")
    con.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
