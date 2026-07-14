# Write-up — From messy spreadsheets to one commercial decision

*A non-technical walkthrough for a business stakeholder. Think of each `##` as a slide.*

---

## 1. The situation

The business had two years of sales sitting in raw spreadsheets — about
**1.07 million rows** across two files. Useful in theory, unusable in practice:
duplicated lines, cancellations mixed in with sales, postage counted as if it
were product revenue, and a quarter of orders with no customer attached.

You can't make decisions on that. So the first job was to make it trustworthy.

---

## 2. What we built (in one picture)

```
  Raw spreadsheets          BigQuery warehouse                 Decisions
  ─────────────────         ────────────────────────           ──────────────
  online_retail_II.xlsx  →  RAW  (faithful copy)          →
   (1.07m messy rows)        STAGING (cleaned + flagged)        "Who are our best
                             CUSTOMERS / PRODUCTS / SALES        customers, and who
                             (tested, analytics-ready)           is slipping away?"
```

We cleaned the things that quietly distort every report:
- removed **12,000 duplicate lines**,
- **netted out cancellations** so revenue is real, not inflated,
- pulled out **£322k of postage** that was masquerading as product sales,
- and separated **known customers (86% of revenue)** from anonymous guests (14%).

The result is a set of clean tables anyone can query in seconds, with automated
tests that stop bad data reaching a report.

---

## 3. What the data says

**Net product revenue over the two years: £18.9m.** Three findings stand out:

1. **The business runs on a small core.** The **top 20% of customers drive 77%**
   of revenue. One group — our "Champions" — is **~24% of buyers but 70% of
   revenue**.
2. **Customers come back.** **76% of buyers reorder.** This is a repeat-purchase
   business, so the growth lever is keeping people buying, not just finding new
   ones.
3. **A valuable group is quietly leaving.** **839 customers** who used to order
   6+ times and spend ~£1,700 each **haven't bought in about a year** — roughly
   **£1.45m** of historical revenue drifting out the door.

---

## 4. The recommendation

**Win back those 839 lapsed high-value customers.**

- **Who:** the 839 proven repeat buyers who've gone quiet (~12 months silent).
- **What:** a personalised win-back email referencing what they used to buy, with
  a short-dated incentive (e.g. *15% off your next order over £150*).
- **Why it's low-risk:** email costs almost nothing, and the discount is only paid
  when someone actually comes back.
- **Likely payoff:** a conservative **10% reactivation** is ~84 customers and
  **£19k–£28k in the first two months**, with much more if they resume their old
  buying rhythm.

---

## 5. How we'll know it worked

We won't guess. We **hold back 20% of the group as a control** and send to the
rest. After **8 weeks** we compare the two: if the customers we contacted come
back at a meaningfully higher rate than the ones we didn't — and the extra revenue
beats the discount cost — the campaign is proven, and we scale it.

---

## 6. What to trust, and what not to

- These are **revenue** figures, not profit — we don't have cost data, so treat
  the payoff as topline.
- The numbers describe the **known** customer base; 14% of revenue is anonymous.
- The dataset is **2009–2011**; in production we'd re-score on live data.

**Bottom line:** the data is now clean and reliable, it points clearly at
retention over acquisition, and there's a specific, measurable campaign we can
launch this week to recover revenue that's currently walking away.
