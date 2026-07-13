"""
app/app.py — lightweight internal explorer for the Online Retail II warehouse.

Reads the local DuckDB warehouse built by local/build_local.py and lets a
non-technical user explore the modelled tables: KPIs, revenue trend, RFM
segments, top customers/products, and geography.

Run:
    pip install -r app/requirements.txt
    python local/build_local.py         # builds local/warehouse.duckdb first
    streamlit run app/app.py
"""

from __future__ import annotations

from pathlib import Path

import duckdb
import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).resolve().parents[1] / "local" / "warehouse.duckdb"

# RFM computed inline (read-only safe — no table creation).
RFM_CTE = """
with base as (
  select customer_sk, customer_id, primary_country,
         date_diff('day', last_purchase_date,
                   (select max(last_purchase_date) + interval 1 day
                    from online_retail.dim_customer)) as recency_days,
         lifetime_orders as frequency,
         lifetime_net_revenue as monetary
  from online_retail.dim_customer
  where lifetime_orders > 0 and lifetime_net_revenue > 0
),
scored as (
  select *,
    6 - ntile(5) over (order by recency_days) as r,
    ntile(5) over (order by frequency)        as f,
    ntile(5) over (order by monetary)         as m
  from base
)
select *,
  case
    when r >= 4 and f >= 4 then 'Champions'
    when r >= 3 and f >= 3 then 'Loyal'
    when r >= 4 and f <= 2 then 'New / Promising'
    when r = 3 and f <= 2 then 'Potential Loyalist'
    when r = 2 and f >= 3 then 'At Risk'
    when r = 2 and f <= 2 then 'Hibernating'
    when r = 1 and f >= 4 then 'Cannot Lose Them'
    when r = 1 and f >= 3 then 'At Risk'
    else 'Lost'
  end as segment
from scored
"""


@st.cache_data
def q(sql: str) -> pd.DataFrame:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        return con.sql(sql).df()
    finally:
        con.close()


st.set_page_config(page_title="Online Retail II — Explorer", layout="wide")
st.title("Online Retail II — warehouse explorer")

if not DB_PATH.exists():
    st.error("warehouse.duckdb not found. Run `python local/build_local.py` first.")
    st.stop()

# --- KPI row --------------------------------------------------------------- #
kpi = q("""
  select
    (select count(*) from online_retail.dim_customer)                          as customers,
    (select count(*) from online_retail.fct_orders where not is_cancellation)  as orders,
    (select sum(line_revenue) from online_retail.fct_sales
       where not is_service_line)                                              as net_revenue
""").iloc[0]
c1, c2, c3 = st.columns(3)
c1.metric("Known customers", f"{int(kpi.customers):,}")
c2.metric("Orders", f"{int(kpi.orders):,}")
c3.metric("Net product revenue", f"£{kpi.net_revenue:,.0f}")

# --- Revenue trend --------------------------------------------------------- #
st.subheader("Monthly net product revenue")
trend = q("""
  select strftime(invoice_date, '%Y-%m') as month,
         sum(line_revenue) as net_revenue
  from online_retail.fct_sales
  where not is_service_line
  group by month order by month
""")
st.bar_chart(trend, x="month", y="net_revenue", height=280)

# --- RFM segments ---------------------------------------------------------- #
st.subheader("Customer segments (RFM)")
seg = q(f"""
  with rfm as ({RFM_CTE})
  select segment,
         count(*) as customers,
         round(sum(monetary)) as net_revenue,
         round(avg(recency_days)) as avg_recency_days,
         round(avg(frequency), 1) as avg_orders
  from rfm group by segment order by net_revenue desc
""")
left, right = st.columns([2, 3])
left.dataframe(seg, use_container_width=True, hide_index=True)
right.bar_chart(seg, x="segment", y="net_revenue", height=320)

st.caption("Win-back target = **At Risk + Cannot Lose Them** "
           "(lapsed, previously high-value customers).")

# --- Top customers & products --------------------------------------------- #
a, b = st.columns(2)
a.subheader("Top 10 customers")
a.dataframe(q("""
  select customer_id, primary_country, lifetime_orders,
         round(lifetime_net_revenue) as net_revenue
  from online_retail.dim_customer
  order by lifetime_net_revenue desc limit 10
"""), use_container_width=True, hide_index=True)

b.subheader("Top 10 products")
b.dataframe(q("""
  select p.description, round(sum(f.line_revenue)) as net_revenue
  from online_retail.fct_sales f
  join online_retail.dim_product p using (product_sk)
  where not f.is_service_line
  group by p.description order by net_revenue desc limit 10
"""), use_container_width=True, hide_index=True)

# --- Geography ------------------------------------------------------------- #
st.subheader("Revenue by country (top 12)")
st.dataframe(q("""
  select country,
         count(distinct invoice_no) as orders,
         round(sum(order_revenue)) as net_revenue
  from online_retail.fct_orders
  where not is_cancellation
  group by country order by net_revenue desc limit 12
"""), use_container_width=True, hide_index=True)
