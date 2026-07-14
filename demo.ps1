# demo.ps1 — reproduce the whole pipeline end to end, locally (no cloud needed).
#
#   Usage:   ./demo.ps1
#
# Runs: extract -> build warehouse + assertions -> RFM analysis -> dashboard.
# Skips the source download automatically if the workbook is already present.

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$xlsx = Join-Path $PSScriptRoot "data/online_retail_II.xlsx"
$loadArgs = @()
if (Test-Path $xlsx) { $loadArgs += "--skip-download" }

Write-Host "`n[1/4] Extract & load (spreadsheet -> RAW parquet)" -ForegroundColor Cyan
python extract/load.py @loadArgs

Write-Host "`n[2/4] Build warehouse + run assertions (DuckDB)" -ForegroundColor Cyan
python local/build_local.py

Write-Host "`n[3/4] RFM analysis (segments + headline numbers)" -ForegroundColor Cyan
python local/rfm_analysis.py

Write-Host "`n[4/4] Render dashboard (docs/dashboard.png)" -ForegroundColor Cyan
try {
    python local/make_dashboard.py
} catch {
    Write-Host "  (dashboard skipped - install matplotlib to enable)" -ForegroundColor Yellow
}

Write-Host "`nDone. Warehouse: local/warehouse.duckdb" -ForegroundColor Green
