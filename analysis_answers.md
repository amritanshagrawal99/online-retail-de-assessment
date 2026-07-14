# Analysis & Commercial Recommendation

*Dataset: UCI Online Retail II (a UK online gift/homeware wholesaler, Dec 2009 – Dec 2011).
All figures below are computed by the modelled tables in this repo and reproduced by
`local/rfm_analysis.py` (`local/analysis_outputs.json`). Money is in GBP.*

**Scope of the numbers.** "Net product revenue" excludes service lines (postage,
manual adjustments, bank fees) and nets out returns/cancellations. Total net
product revenue over the ~24-month window is **£18.9m**, of which **£16.4m (86%)**
is attributable to a known customer and **£2.6m (14%)** is guest/unattributed.

---

## 1. Commercial insights

### Insight 1 — Revenue is extremely concentrated: a small core carries the business
- **Finding:** The top 20% of purchasing customers generate **76.7%** of net
  revenue. One RFM segment — **Champions** (1,435 customers, ~24% of buyers) —
  alone accounts for **70.0%** of revenue (£11.5m).
- **Method:** RFM scoring (Recency, Frequency, Monetary into quintiles) on
  `dim_customer`, plus a Pareto/quintile revenue-share calculation.
- **Why care:** The commercial risk here is **retention, not acquisition**. If a
  few hundred core accounts slip, most of the P&L slips with them. Budget should
  weight "keep the core buying" over "buy more new logos".

### Insight 2 — This behaves like a repeat-purchase (wholesale) business
- **Finding:** **75.6%** of customers who ever purchased placed **2+ orders**
  (4,444 of 5,876). Champions average **~20 orders** each over the period. This
  is not a one-and-done consumer catalogue; buyers come back.
- **Method:** Order-count distribution from `dim_customer.lifetime_orders`.
- **Why care:** Lifetime value and purchase **frequency** are the growth levers,
  not first-purchase conversion. A 10% lift in reorder frequency from the core is
  worth far more than the same % lift in new-customer count.

### Insight 3 — There is a large, recoverable "at-risk" cohort
- **Finding:** **839 customers** fall into **At Risk + Cannot Lose Them** — people
  who historically ordered **6+ times** and averaged **£1,728** net spend, but
  whose **last purchase was ~12 months ago** (avg recency 361 days). Collectively
  they represent **£1.45m** of historical revenue that is quietly lapsing.
- **Method:** RFM recency segmentation — high Frequency/Monetary, low Recency.
- **Why care:** This is the single most **actionable** finding: a specific,
  sizeable, previously-proven set of buyers, addressable by name, drifting away.

---

## 2. The recommendation — a targeted win-back campaign

**Run a win-back campaign against the 839 lapsed high-value customers
(At Risk + Cannot Lose Them).**

- **Target segment (who, and how identified):** The 839 customers scored
  RFM-low on Recency but high on Frequency & Monetary — i.e. proven repeat buyers
  (avg 6.2 lifetime orders, avg £1,728 net spend) who have not ordered in ~12
  months. Pulled directly from `online_retail.rfm` where
  `segment IN ('At Risk','Cannot Lose Them')`.
- **Mechanic / offer:** A personalised win-back email (and where a phone/account
  manager exists, a call) referencing the categories they used to buy, plus a
  **time-limited incentive** — e.g. *15% off your next order over £150, valid 3
  weeks* (or free postage, which this business already itemises). Incentive is
  paid only on redemption, so cost scales with success.
- **Expected commercial impact (with assumptions stated):** Win-back campaigns to
  *known, previously-active* buyers typically reactivate **8–12%**. On 839
  targets that is **~67–101 reactivations**. Segment average order value is
  **~£280** (£1,728 ÷ 6.2 orders). A conservative "one order back" gives
  **£19k–£28k** of incremental revenue in the first ~8 weeks. If even a third of
  reactivations resume their historical cadence, the annualised value runs into
  **six figures** against a near-zero marginal email cost. *Assumptions: 8–12%
  reactivation, £280 AOV held, 15% discount on redeemed orders only.*
- **Measurement (how we'd know within N weeks):** **Randomised holdout.** Hold
  back 20% of the 839 as an untouched control; send to the other 80%. Over an
  **8-week** window compare, treatment vs control: (1) reactivation rate
  (% placing ≥1 order), (2) revenue per targeted customer, (3) margin after
  discount. **Success = treatment reactivation rate exceeds control by ≥3–5pp
  with positive incremental margin.** The holdout is what separates "the campaign
  worked" from "these people were going to buy anyway".

---

## 3. Assumptions, caveats & data quality

### Assumptions on the messier data (and why)
- **Guest rows (23% of lines, £2.6m / 14% of product revenue) have no
  `customer_id`.** Kept in company-level revenue totals but **excluded from
  customer-level** analytics (RFM/LTV) — you cannot segment an anonymous buyer.
- **Cancellations** (invoices starting `C`, negative quantity) are **kept and
  netted out**, so revenue reflects true realised sales, not gross.
- **Service codes** (`POST`, `DOT`, `M`, `BANK CHARGES`, `AMAZONFEE`, `gift_*`,
  `TEST*`) are **excluded from product analytics**. `DOT` alone is ~£322k of
  postage that would otherwise inflate "product" revenue.
- **12,133 exact-duplicate lines** are de-duplicated in staging via a natural-key
  hash, so they are not double-counted.
- Prices assumed **GBP**; `invoice_date` treated as **store-local** (no TZ in
  source); `customer_id` cast from the Excel float artifact (`13085.0` → `13085`).

### What I'd flag to a client as a limitation (not false precision)
- **No cost/margin data** — every figure is **revenue, not profit**. The impact
  estimate is topline; net margin depends on the discount and fulfilment cost.
- **14% of revenue is unattributable** (guests), so all customer metrics are a
  view of the *known* base, not the whole business.
- **The data is 2009–2011** — segments describe *that* period; a live deployment
  would re-score on current data.
- **UK is ~85% of revenue**; non-UK reads are thin and shouldn't be over-indexed.
- We see *transactions only* — no marketing touch, web session, or channel data,
  so we can't attribute *why* someone bought.

### Top 3 data-quality checks I'd run in production
1. **Volume & freshness:** row count per load within an expected band, and
   `MAX(invoice_date)` / `MAX(_loaded_at)` advancing each run (freshness SLA).
2. **Null-rate & validity tolerances:** `customer_id` null-rate ≈ its historical
   ~23% (alert on a jump), `price`/`quantity` not-null, and sign checks
   (unexpected surge in negative prices/quantities).
3. **Keys & referential integrity:** `dim_*` surrogate keys **unique & not-null**;
   every `fct_sales` FK resolves to a dimension (no orphans). *(These exact
   checks run in `local/build_local.py` and as dbt tests in `dbt_project/`.)*

### Detecting a silent schema/shape change or stale source
- **Schema contract test:** assert the expected column set + types on every load;
  fail loudly if a column is added/removed/re-typed (BigQuery schema, or dbt
  `dbt source freshness` + a column-contract test).
- **Distribution monitors:** track row count, null-rates, and revenue per load;
  alert on z-score deviations from the trailing baseline (the same anomaly method
  as Q3), which catches "shape changed but still valid SQL".
- **Freshness SLA:** alert if `MAX(invoice_date)` hasn't moved or `_loaded_at` is
  older than the expected cadence — the classic "upstream export silently stopped".

---

## 4. Executive memo (≤200 words)

> **To:** CFO / COO
> **From:** Data & AI Engineering
> **Re:** What we built, and the one move that pays for it
>
> We took two years of raw, messy sales files (~1.07m rows across two
> spreadsheets) and turned them into a clean, trustworthy warehouse: raw data →
> BigQuery → tested, modelled customer/product/sales tables you can query in
> seconds. Along the way we corrected the things that quietly distort reporting —
> £322k of postage miscounted as product sales, 12k duplicate lines, cancellations
> not netted out, and 23% of orders with no customer attached.
>
> The headline: **77% of revenue comes from the top 20% of customers**, and this
> is a **repeat-purchase business** (76% of buyers reorder). That reframes the
> priority as **retention over acquisition**.
>
> We found **839 previously high-value customers — worth £1.45m historically —
> who have stopped ordering in the last year.** 
>
> **Recommended next step:** a targeted win-back campaign to those 839, measured
> against a randomised holdout so we can prove the incremental effect within 8
> weeks. Near-zero cost, and even a conservative 10% reactivation returns tens of
> thousands in the first two months. We can launch this week.

---

## 5. LLM use case

**"Ask-your-warehouse" natural-language querying over the marts.**

- **Example user prompt:** *"Which 20 customers in Germany haven't ordered in the
  last 6 months but used to spend the most?"*
- **What data is retrieved:** The LLM is given the **schema + column descriptions**
  of `dim_customer`, `dim_product`, `fct_sales` (not the raw rows). It generates a
  BigQuery query, which runs against the marts; only the **result set** returns.
- **What the LLM outputs:** The generated SQL (shown for transparency) **plus** a
  one-paragraph plain-English summary of the answer and a suggested next action.
- **Main risk:** **Hallucinated columns/joins → confidently wrong numbers**, and
  cost/security if it can touch raw PII or run expensive scans.
- **Mitigation:** Constrain it to a **read-only, curated semantic layer** (only
  the marts, with a byte-scanned/query cost cap); **validate generated SQL** (dry-
  run + allow-list of tables) before execution; **show the SQL** so a human can
  sanity-check; and evaluate against a **golden set** of question→SQL pairs before
  trusting it. Start with *assisted* answers (human in the loop), not autonomous.

*A natural extension is **automated insight narration**: a scheduled job that runs
the anomaly query (Q3) and has the LLM write the weekly "here's what changed and
why it matters" note — same guardrails apply.*
