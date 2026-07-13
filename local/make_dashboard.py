"""
local/make_dashboard.py
=======================
Render a static PNG dashboard from the built warehouse (docs/dashboard.png).
A committable, browser-free snapshot of the same views the Streamlit app shows.
Run after local/build_local.py.
"""
from __future__ import annotations

from pathlib import Path

import duckdb
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import FuncFormatter

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "local" / "warehouse.duckdb"
OUT = ROOT / "docs" / "dashboard.png"

RFM = """
with base as (
  select date_diff('day', last_purchase_date,
           (select max(last_purchase_date)+interval 1 day from online_retail.dim_customer)) recency_days,
         lifetime_orders frequency, lifetime_net_revenue monetary
  from online_retail.dim_customer where lifetime_orders>0 and lifetime_net_revenue>0),
scored as (select *, 6-ntile(5) over(order by recency_days) r, ntile(5) over(order by frequency) f from base)
select case
  when r>=4 and f>=4 then 'Champions' when r>=3 and f>=3 then 'Loyal'
  when r>=4 and f<=2 then 'New/Promising' when r=3 and f<=2 then 'Potential Loyalist'
  when r=2 and f>=3 then 'At Risk' when r=2 and f<=2 then 'Hibernating'
  when r=1 and f>=4 then 'Cannot Lose Them' when r=1 and f>=3 then 'At Risk'
  else 'Lost' end segment, sum(monetary) rev
from scored group by segment order by rev desc
"""

con = duckdb.connect(str(DB), read_only=True)
trend = con.sql("""select strftime(invoice_date,'%Y-%m') m, sum(line_revenue) rev
                   from online_retail.fct_sales where not is_service_line group by m order by m""").df()
seg = con.sql(RFM).df()
geo = con.sql("""select country, sum(order_revenue) rev from online_retail.fct_orders
                 where not is_cancellation group by country order by rev desc limit 8""").df()
kpi = con.sql("""select (select count(*) from online_retail.dim_customer) c,
                        (select sum(line_revenue) from online_retail.fct_sales where not is_service_line) r
              """).df().iloc[0]
con.close()

gbp = FuncFormatter(lambda x, _: f"£{x/1e6:.1f}m" if x >= 1e6 else f"£{x/1e3:.0f}k")
plt.style.use("seaborn-v0_8-whitegrid")
fig = plt.figure(figsize=(14, 8))
fig.suptitle(f"Online Retail II  —  £{kpi.r/1e6:,.1f}m net product revenue  ·  "
             f"{int(kpi.c):,} customers  (2009–2011)", fontsize=15, fontweight="bold")

ax1 = plt.subplot2grid((2, 2), (0, 0), colspan=2)
ax1.bar(trend.m, trend.rev, color="#2563eb")
ax1.set_title("Monthly net product revenue", loc="left", fontweight="bold")
ax1.yaxis.set_major_formatter(gbp)
ax1.set_xticks(range(0, len(trend), 2))
ax1.set_xticklabels(trend.m[::2], rotation=45, ha="right", fontsize=8)

ax2 = plt.subplot2grid((2, 2), (1, 0))
colors = ["#dc2626" if s in ("At Risk", "Cannot Lose Them") else "#2563eb" for s in seg.segment]
ax2.barh(seg.segment[::-1], seg.rev[::-1], color=colors[::-1])
ax2.set_title("Revenue by RFM segment  (red = win-back target)", loc="left", fontweight="bold")
ax2.xaxis.set_major_formatter(gbp)
ax2.tick_params(labelsize=8)

ax3 = plt.subplot2grid((2, 2), (1, 1))
ax3.barh(geo.country[::-1], geo.rev[::-1], color="#0891b2")
ax3.set_title("Revenue by country (top 8)", loc="left", fontweight="bold")
ax3.xaxis.set_major_formatter(gbp)
ax3.tick_params(labelsize=8)

plt.tight_layout(rect=[0, 0, 1, 0.96])
OUT.parent.mkdir(exist_ok=True)
plt.savefig(OUT, dpi=130, bbox_inches="tight")
print(f"wrote {OUT}")
