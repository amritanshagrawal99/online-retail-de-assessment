# Extraction notes — assumptions, encoding, malformed rows

**Source:** UCI *Online Retail II* — `https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip`
A single workbook `online_retail_II.xlsx` with two sheets: `Year 2009-2010` and
`Year 2010-2011`. Together: **1,067,371 rows**, invoice dates **2009-12-01 → 2011-12-09**.

## What the extract step does (and deliberately does *not* do)
The extract is intentionally thin. It only:
1. Unions the two year-sheets into one frame.
2. Renames columns to snake_case (`Customer ID` → `customer_id`, etc.).
3. Stamps ingestion metadata: `_source_file`, `_source_sheet`, `_source_row`,
   `_loaded_at`, and a deterministic `_record_hash` (MD5 of the natural line).
4. Lands the result to `data/online_retail_raw.parquet` and, optionally, to
   BigQuery `online_retail_raw.online_retail_raw`.

**No cleaning, filtering, or business logic happens here.** Everything else
(typing repairs, dedup, cancellation handling, service-line flagging) lives in
version-controlled SQL so it is auditable and re-runnable. The raw layer stays a
faithful, re-loadable copy of the source.

## Type / encoding assumptions
| Field | Source reality | Decision |
|---|---|---|
| `invoice` | Mixed int (`489434`) and string (`C489449` = cancellation) | Land as **STRING**. Casting would corrupt cancellation codes. |
| `stock_code` | Mixed 5-digit product codes + service codes (`POST`,`DOT`,`M`,`BANK CHARGES`,`AMAZONFEE`,`TEST001`,`gift_0001_*`) | Land as **STRING**; classify in staging. |
| `customer_id` | Excel float → arrives as `13085.0`; blanks are `NaN` | Strip `.0`, blanks → `NULL`, land as **STRING**; cast to INT64 in staging. |
| `quantity` | Integer; can be negative on cancellations | Land as **INT64** (nullable). |
| `price` | Float; can be `0` or negative on adjustments | Land as **FLOAT64**. |
| `invoice_date` | Excel datetime | Land as **TIMESTAMP** (UTC-naive; treated as store-local). |
| `description` | Free text; 4,382 nulls; some non-ASCII | Land as **STRING**, UTF-8. |
| `country` | 43 distinct; includes `Unspecified`, `European Community` | Land as **STRING**. |

## Known malformed / messy rows (counts from the real file)
These are **kept in raw** and handled downstream — raw never drops data.

| Issue | Rows | Handled in |
|---|---|---|
| Null `customer_id` (guest / unattributed) | 243,007 (~23%) | `stg` flag `has_customer`; excluded from customer-level RFM |
| Cancellations (`invoice` starts with `C`) | 19,494 | `stg` flag `is_cancellation`; netted out of revenue |
| `quantity <= 0` | 22,950 | `stg` flag `is_return_or_zero` |
| `price <= 0` (mostly exactly 0) | 6,207 | `stg` flag `is_nonpositive_price` |
| Exact duplicate lines | 12,133 | de-duplicated in `stg` via `_record_hash` + `ROW_NUMBER` |
| Service / non-product `stock_code` (`POST`,`DOT`,`M`…) | 6,093 (`DOT` alone = £322k) | `stg` flag `is_service_line`; excluded from product analytics |
| Null `description` | 4,382 | coalesced to `'UNKNOWN'` in `stg` |

## Manual steps
None required. `python extract/load.py` downloads, unzips, unions, and lands the
data with no console clicks. (`--skip-download` reuses an already-downloaded file.)

## Reproduce
```bash
pip install -r extract/requirements.txt
python extract/load.py                        # local parquet only
python extract/load.py --project <gcp-proj>   # also load to BigQuery
```
