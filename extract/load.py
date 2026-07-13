"""
extract/load.py
================
Extract & load step for the Online Retail II dataset.

What it does
------------
1. Downloads the UCI "Online Retail II" workbook (if not already present).
2. Reads BOTH year sheets ("Year 2009-2010", "Year 2010-2011") and unions them.
3. Normalises column names to snake_case and stamps ingestion metadata
   (_source_file, _source_sheet, _source_row, _loaded_at).
4. Lands the result as an immutable RAW artifact: data/online_retail_raw.parquet.
5. Optionally loads that RAW artifact into BigQuery `<project>.<raw_dataset>.online_retail_raw`.

Design notes
------------
* The extract step is deliberately "dumb": it faithfully lands the source with
  MINIMAL transformation. All cleaning/typing/business logic lives in SQL
  (staging -> marts). This keeps the raw layer a re-loadable source of truth and
  makes every downstream decision auditable in version-controlled SQL.
* The only transforms done here are (a) snake_case column names and (b) adding
  ingestion metadata. Both are ingestion-standard and lossless.
* customer_id is landed as STRING on purpose. Excel stores it as a float, so
  values arrive as "13085.0" and blanks as NaN. Casting/repairing that is a
  modelling decision, so it happens in staging (stg_online_retail), not here.

Usage
-----
    # Local only (produces the parquet RAW artifact used by DuckDB + BigQuery):
    python extract/load.py

    # Also load into BigQuery:
    python extract/load.py --project my-gcp-project --raw-dataset online_retail_raw

Idempotency
-----------
The BigQuery load uses WRITE_TRUNCATE, so re-running fully replaces the raw
table rather than appending duplicates. The local parquet is overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
UCI_URL = "https://archive.ics.uci.edu/static/public/502/online+retail+ii.zip"
DATA_DIR = Path(__file__).resolve().parents[1] / "data"
ZIP_PATH = DATA_DIR / "online_retail_ii.zip"
XLSX_PATH = DATA_DIR / "online_retail_II.xlsx"
RAW_PARQUET = DATA_DIR / "online_retail_raw.parquet"
SHEETS = ["Year 2009-2010", "Year 2010-2011"]

# Source column name -> raw (snake_case) column name.
COLUMN_MAP = {
    "Invoice": "invoice",
    "StockCode": "stock_code",
    "Description": "description",
    "Quantity": "quantity",
    "InvoiceDate": "invoice_date",
    "Price": "price",
    "Customer ID": "customer_id",
    "Country": "country",
}


# --------------------------------------------------------------------------- #
# Step 1 — download source
# --------------------------------------------------------------------------- #
def download_source() -> None:
    """Download + unzip the UCI workbook if the .xlsx is not already present."""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if XLSX_PATH.exists():
        print(f"[extract] Source already present: {XLSX_PATH.name}")
        return

    import requests  # imported lazily so the parquet->BQ path has fewer deps

    print(f"[extract] Downloading {UCI_URL} ...")
    resp = requests.get(UCI_URL, timeout=180)
    resp.raise_for_status()
    ZIP_PATH.write_bytes(resp.content)
    print(f"[extract] Downloaded {len(resp.content):,} bytes -> {ZIP_PATH.name}")

    with zipfile.ZipFile(ZIP_PATH) as zf:
        zf.extractall(DATA_DIR)
    print(f"[extract] Unzipped -> {XLSX_PATH.name}")


# --------------------------------------------------------------------------- #
# Step 2 — read + union + stamp metadata
# --------------------------------------------------------------------------- #
def build_raw_frame() -> pd.DataFrame:
    """Read both sheets, union, snake_case columns, add ingestion metadata."""
    loaded_at = datetime.now(timezone.utc)
    frames: list[pd.DataFrame] = []

    for sheet in SHEETS:
        print(f"[extract] Reading sheet: {sheet}")
        df = pd.read_excel(XLSX_PATH, sheet_name=sheet, dtype={"Customer ID": "object"})
        df = df.rename(columns=COLUMN_MAP)

        # Pin source dtypes so the RAW artifact matches the BigQuery schema and
        # parquet inference is deterministic. invoice/stock_code hold mixed
        # int/str values in the workbook (e.g. 489434 and 'C489449'), so they
        # MUST be landed as STRING.
        for col in ["invoice", "stock_code", "description", "country"]:
            df[col] = df[col].astype("string")
        df["quantity"] = df["quantity"].astype("Int64")
        df["price"] = df["price"].astype("float64")
        df["invoice_date"] = pd.to_datetime(df["invoice_date"], errors="coerce")

        # customer_id: land as clean STRING ("13085.0" -> "13085", NaN -> None).
        df["customer_id"] = df["customer_id"].map(_clean_customer_id).astype("string")

        # Ingestion metadata (audit / lineage).
        df["_source_file"] = XLSX_PATH.name
        df["_source_sheet"] = sheet
        df["_source_row"] = df.index + 2  # +2: header row + 1-based Excel rows
        df["_loaded_at"] = loaded_at
        frames.append(df)

    raw = pd.concat(frames, ignore_index=True)

    # Deterministic content hash of the natural line (used later as a stable key
    # and for exact-duplicate detection in staging). Computed here so the same
    # value is reproducible whether the consumer is BigQuery or DuckDB.
    raw["_record_hash"] = raw.apply(_record_hash, axis=1)

    print(f"[extract] Unioned raw rows: {len(raw):,}")
    return raw


def _clean_customer_id(value) -> str | None:
    """'13085.0' -> '13085'; blanks/NaN -> None. Kept as STRING in raw."""
    if pd.isna(value):
        return None
    text = str(value).strip()
    if text == "":
        return None
    # Strip the Excel float artifact (trailing '.0').
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _record_hash(row: pd.Series) -> str:
    """MD5 of the natural line. Portable: BigQuery TO_HEX(MD5(..)) == this."""
    parts = [
        str(row["invoice"]),
        str(row["stock_code"]),
        "" if pd.isna(row["quantity"]) else str(int(row["quantity"])),
        "" if pd.isna(row["invoice_date"]) else str(row["invoice_date"]),
        "" if pd.isna(row["price"]) else f'{float(row["price"]):.4f}',
        "" if row["customer_id"] is None else str(row["customer_id"]),
    ]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Step 3 — write RAW artifact
# --------------------------------------------------------------------------- #
def write_parquet(raw: pd.DataFrame) -> None:
    raw.to_parquet(RAW_PARQUET, index=False)
    print(f"[extract] Wrote RAW artifact -> {RAW_PARQUET} ({len(raw):,} rows)")


# --------------------------------------------------------------------------- #
# Step 4 — optional BigQuery load
# --------------------------------------------------------------------------- #
def load_to_bigquery(raw: pd.DataFrame, project: str, dataset: str) -> None:
    """Load the RAW frame into BigQuery with WRITE_TRUNCATE (idempotent)."""
    from google.cloud import bigquery

    client = bigquery.Client(project=project)
    dataset_ref = bigquery.Dataset(f"{project}.{dataset}")
    dataset_ref.location = "EU"  # Online Retail II is a UK dataset
    client.create_dataset(dataset_ref, exists_ok=True)

    table_id = f"{project}.{dataset}.online_retail_raw"
    schema = [
        bigquery.SchemaField("invoice", "STRING"),
        bigquery.SchemaField("stock_code", "STRING"),
        bigquery.SchemaField("description", "STRING"),
        bigquery.SchemaField("quantity", "INT64"),
        bigquery.SchemaField("invoice_date", "TIMESTAMP"),
        bigquery.SchemaField("price", "FLOAT64"),
        bigquery.SchemaField("customer_id", "STRING"),
        bigquery.SchemaField("country", "STRING"),
        bigquery.SchemaField("_source_file", "STRING"),
        bigquery.SchemaField("_source_sheet", "STRING"),
        bigquery.SchemaField("_source_row", "INT64"),
        bigquery.SchemaField("_loaded_at", "TIMESTAMP"),
        bigquery.SchemaField("_record_hash", "STRING"),
    ]
    job_config = bigquery.LoadJobConfig(
        schema=schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    print(f"[extract] Loading {len(raw):,} rows -> {table_id} (WRITE_TRUNCATE) ...")
    job = client.load_table_from_dataframe(raw, table_id, job_config=job_config)
    job.result()
    table = client.get_table(table_id)
    print(f"[extract] BigQuery load complete: {table.num_rows:,} rows in {table_id}")


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #
def main() -> int:
    parser = argparse.ArgumentParser(description="Extract & load Online Retail II.")
    parser.add_argument("--project", help="GCP project id (enables BigQuery load).")
    parser.add_argument("--raw-dataset", default="online_retail_raw",
                        help="BigQuery dataset for the raw table.")
    parser.add_argument("--skip-download", action="store_true",
                        help="Assume the .xlsx is already present.")
    args = parser.parse_args()

    if not args.skip_download:
        download_source()

    raw = build_raw_frame()
    write_parquet(raw)

    if args.project:
        load_to_bigquery(raw, args.project, args.raw_dataset)
    else:
        print("[extract] No --project given; skipped BigQuery load "
              "(parquet artifact is ready for DuckDB or a later bq load).")

    print("[extract] Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
