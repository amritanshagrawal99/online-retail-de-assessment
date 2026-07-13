"""
local/build_local.py
====================
Run the ACTUAL BigQuery SQL (sql/02, sql/03) against a local DuckDB engine, so
the pipeline's logic is proven correct end-to-end without needing a GCP account.

Why this is trustworthy
-----------------------
The SQL files are BigQuery-canonical. DuckDB understands ~95% of that dialect
verbatim (CTEs, QUALIFY, ROW_NUMBER, TO_HEX(MD5()), STARTS_WITH, SUBSTR,
window funcs, DATE()). The ONLY differences are a tiny, documented shim:

    SAFE_CAST(x AS T)  -> TRY_CAST(x AS T)     (token replace)
    FLOAT64            -> DOUBLE               (type name)
    SAFE_DIVIDE(a, b)  -> DuckDB MACRO
    FORMAT_DATE(f, d)  -> DuckDB MACRO (strftime)

Nothing about the model logic changes — only surface syntax. See SHIM below.

Outputs
-------
* local/warehouse.duckdb   — the built warehouse (raw + stg + dims + facts)
* prints assertions (dedup, referential integrity, grain) — all must pass
* prints the 5 analytics queries' results
"""

from __future__ import annotations

import re
import sys
import io
from pathlib import Path

import duckdb

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
RAW_PARQUET = ROOT / "data" / "online_retail_raw.parquet"
DB_PATH = ROOT / "local" / "warehouse.duckdb"

# --- the documented BigQuery -> DuckDB shim ------------------------------- #
def shim(sql: str) -> str:
    sql = sql.replace("`", "")                       # BigQuery quoting -> none
    sql = re.sub(r"\bSAFE_CAST\b", "TRY_CAST", sql)
    sql = re.sub(r"\bFLOAT64\b", "DOUBLE", sql)
    return sql


def split_statements(sql: str) -> list[str]:
    """Split a script on ';' at statement end (our SQL has no embedded ';')."""
    return [s.strip() for s in sql.split(";") if s.strip()]


def rule(title: str) -> None:
    print("\n" + "=" * 70 + f"\n{title}\n" + "=" * 70)


def main() -> int:
    if not RAW_PARQUET.exists():
        print("RAW artifact missing. Run: python extract/load.py")
        return 1

    if DB_PATH.exists():
        DB_PATH.unlink()
    con = duckdb.connect(str(DB_PATH))

    # DuckDB macros that emulate the two BigQuery functions we use.
    con.execute("CREATE SCHEMA IF NOT EXISTS online_retail")
    con.execute("CREATE OR REPLACE MACRO SAFE_DIVIDE(a, b) AS "
                "CASE WHEN b = 0 THEN NULL ELSE a / b END")
    con.execute("CREATE OR REPLACE MACRO FORMAT_DATE(f, d) AS strftime(d, f)")

    # RAW: land the parquet artifact exactly as extract/load.py produced it.
    con.execute("""
        CREATE OR REPLACE TABLE online_retail.raw_online_retail AS
        SELECT * FROM read_parquet(?)
    """, [str(RAW_PARQUET)])
    raw_n = con.sql("SELECT COUNT(*) FROM online_retail.raw_online_retail").fetchone()[0]
    rule("BUILD")
    print(f"raw_online_retail loaded: {raw_n:,} rows")

    # Run the real transform + analytics SQL through the shim.
    for f in ["sql/02_load_transform.sql"]:
        for stmt in split_statements(shim((ROOT / f).read_text(encoding="utf-8"))):
            con.execute(stmt)
        print(f"executed {f}")

    for tbl in ["stg_online_retail", "dim_customer", "dim_product",
                "fct_sales", "fct_orders"]:
        n = con.sql(f"SELECT COUNT(*) FROM online_retail.{tbl}").fetchone()[0]
        print(f"  {tbl:22} {n:>12,} rows")

    # ----------------------------------------------------------------- #
    # ASSERTIONS — the pipeline is only "verified" if these all hold.
    # ----------------------------------------------------------------- #
    rule("ASSERTIONS")
    checks = {
        "stg == raw minus exact-dup lines":
            con.sql("""
              SELECT (SELECT COUNT(DISTINCT _record_hash) FROM online_retail.raw_online_retail)
                   = (SELECT COUNT(*) FROM online_retail.stg_online_retail)
            """).fetchone()[0],
        "fct_sales grain == stg grain":
            con.sql("""
              SELECT (SELECT COUNT(*) FROM online_retail.fct_sales)
                   = (SELECT COUNT(*) FROM online_retail.stg_online_retail)
            """).fetchone()[0],
        "no NULL product_sk in fct_sales":
            con.sql("SELECT COUNT(*)=0 FROM online_retail.fct_sales WHERE product_sk IS NULL").fetchone()[0],
        "dim_customer.customer_sk is unique":
            con.sql("""
              SELECT COUNT(*) = COUNT(DISTINCT customer_sk) FROM online_retail.dim_customer
            """).fetchone()[0],
        "fct_sales -> dim_product RI (no orphans)":
            con.sql("""
              SELECT COUNT(*)=0 FROM online_retail.fct_sales f
              LEFT JOIN online_retail.dim_product d USING(product_sk)
              WHERE d.product_sk IS NULL
            """).fetchone()[0],
        "fct_sales -> dim_customer RI (non-guest)":
            con.sql("""
              SELECT COUNT(*)=0 FROM online_retail.fct_sales f
              LEFT JOIN online_retail.dim_customer d USING(customer_sk)
              WHERE f.customer_sk IS NOT NULL AND d.customer_sk IS NULL
            """).fetchone()[0],
    }
    all_ok = True
    for name, ok in checks.items():
        all_ok &= bool(ok)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")

    # ----------------------------------------------------------------- #
    # ANALYTICS — run sql/03 verbatim (through the shim) and show output.
    # ----------------------------------------------------------------- #
    labels = ["Q1 Top 10 customers", "Q2 Monthly revenue trend",
              "Q3 Outlier orders (z>3)", "Q4 Revenue by country",
              "Q5 Top products 2011"]
    stmts = split_statements(shim((ROOT / "sql/03_analytics_queries.sql").read_text(encoding="utf-8")))
    rule("ANALYTICS (sql/03)")
    for label, stmt in zip(labels, stmts):
        print(f"\n--- {label} ---")
        df = con.sql(stmt).df()
        # Trim long trends for readability
        print(df.head(12).to_string(index=False))

    con.close()
    rule("RESULT")
    print("ALL ASSERTIONS PASSED" if all_ok else "SOME ASSERTIONS FAILED")
    return 0 if all_ok else 2


if __name__ == "__main__":
    sys.exit(main())
