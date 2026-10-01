"""Collect USDA's monthly WASDE report, every line as published at the time.

The World Agricultural Supply and Demand Estimates (WASDE) is USDA's monthly
balance sheet for grains, oilseeds, cotton, sugar, meat and dairy: production,
use, trade and stocks for the US and the world, each a projection for the
current season and an estimate for the one before. USDA's Office of the Chief
Economist publishes every report's figures as they appeared on release day,
so this table holds each vintage, not just the latest revision. That is what
a fair benchmark needs: what USDA expected at the time.

Files (live-checked 2026-10-01), all the same 16-column CSV layout:

- April 2010 to December 2015, and January 2016 to December 2020: one ZIP each.
- January 2021 on: one CSV per month, ``oce-wasde-report-data-YYYY-MM.csv``.
  A corrected reissue is named ``...-V2.csv`` and replaces the first file,
  so the newest version is tried first.

usda.gov rejects requests that do not look like a browser (403), so requests
carry ordinary browser headers. Reports come out around the 10th-12th of each
month; a month with no file yet returns 404 and is skipped.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date

import duckdb
import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import get_connection, write_raw

logger = logging.getLogger(__name__)

SOURCE = "usda_wasde"
TABLE = "usda_wasde"
BASE_URL = "https://www.usda.gov/sites/default/files/documents/"
ARCHIVES = [
    "oce-wasde-report-data-2010-04-to-2015-12.zip",
    "oce-wasde-report-data-2016-01-to-2020-12.zip",
]
FIRST_MONTHLY = (2021, 1)
#: Months re-fetched on every run, for reissued (V2) files.
RECENT_MONTHS = 3
VERSIONS = ["-V3", "-V2", ""]
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.usda.gov/historical-wasde-report-data-3",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "same-origin",
}
TIMEOUT = 120

# CSV column -> output column
_COLUMNS = {
    "WasdeNumber": "wasde_number",
    "ReportTitle": "report_title",
    "Attribute": "attribute",
    "ReliabilityProjection": "reliability_projection",
    "Commodity": "commodity",
    "Region": "region",
    "MarketYear": "market_year",
    "ProjEstFlag": "proj_est_flag",
    "AnnualQuarterFlag": "period",
    "Value": "value",
    "Unit": "unit",
    "ReleaseDate": "release_date",
}


def _get(url: str) -> requests.Response | None:
    """The response, or None for a file that does not exist (404)."""
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    if resp.status_code == 404:
        return None
    resp.raise_for_status()
    return resp


def parse_wasde_csv(data: bytes) -> pl.DataFrame:
    """Normalize one WASDE CSV into ``usda_wasde`` columns."""
    raw = pl.read_csv(io.BytesIO(data), infer_schema=False)
    missing = set(_COLUMNS) - set(raw.columns)
    if missing:
        raise ValueError(f"WASDE CSV is missing columns: {sorted(missing)}")
    df = raw.select([pl.col(c).alias(o) for c, o in _COLUMNS.items()])
    text = [c for c in df.columns if c not in ("wasde_number", "value", "release_date")]
    return df.with_columns(
        *[pl.col(c).str.strip_chars().replace("", None) for c in text],
        pl.col("wasde_number").cast(pl.Int32, strict=False),
        # Values look like "1975.00" or ".44"; a few cells are blank.
        pl.col("value").str.strip_chars().cast(pl.Float64, strict=False),
        pl.col("release_date").str.to_date("%Y-%m-%d", strict=False),
    ).filter(pl.col("release_date").is_not_null()).with_columns(
        pl.col("release_date").dt.year().cast(pl.Int32).alias("release_year"),
        pl.lit(SOURCE).alias("source"),
    )


def fetch_month(year: int, month: int) -> pl.DataFrame | None:
    """One month's report (newest reissue first), or None if not published."""
    for version in VERSIONS:
        name = f"oce-wasde-report-data-{year:04d}-{month:02d}{version}.csv"
        resp = _get(BASE_URL + name)
        if resp is None or "csv" not in resp.headers.get("Content-Type", ""):
            continue
        logger.info("WASDE %s", name)
        return parse_wasde_csv(resp.content)
    return None


def fetch_archive(name: str) -> pl.DataFrame:
    resp = _get(BASE_URL + name)
    if resp is None:
        raise ValueError(f"WASDE archive not found: {name}")
    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csvs = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        frames = [parse_wasde_csv(zf.read(n)) for n in csvs]
    logger.info("WASDE archive %s: %d rows", name, sum(f.height for f in frames))
    return pl.concat(frames, how="vertical")


def _months(start: tuple[int, int], end: tuple[int, int]) -> list[tuple[int, int]]:
    out = []
    year, month = start
    while (year, month) <= end:
        out.append((year, month))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return out


def _stored_months() -> set[tuple[int, int]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT year(release_date), month(release_date) FROM {TABLE} "
            "WHERE source = ?",
            [SOURCE],
        ).fetchall()
    except duckdb.CatalogException:
        return set()
    finally:
        conn.close()
    return {(int(r[0]), int(r[1])) for r in rows}


def collect_wasde(
    *,
    today: date | None = None,
    tracker: SourceTracker | None = None,
    bulk_backfill: bool | None = None,
) -> int:
    """Collect WASDE reports into ``usda_wasde``.

    Every run re-fetches the last ``RECENT_MONTHS`` months. When bulk
    backfill is allowed (CI), the 2010-2020 archives are loaded if any of
    those years is missing, and monthly files since 2021 that are not stored
    yet are filled in.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()
    if bulk_backfill is None:
        bulk_backfill = settings.allow_bulk_backfill
    today = today or date.today()
    end = (today.year, today.month)
    recent = _months(FIRST_MONTHLY, end)[-RECENT_MONTHS:]

    months = list(recent)
    archives: list[str] = []
    if bulk_backfill:
        stored = _stored_months()
        months = [m for m in _months(FIRST_MONTHLY, end) if m not in stored or m in recent]
        if not any(y <= 2020 for y, _ in stored):
            archives = ARCHIVES

    with TimedCollector(tracker, SOURCE) as tc:
        written = 0
        for name in archives:
            df = fetch_archive(name)
            tc.rows_fetched += df.height
            written += write_raw(SOURCE, df, table_name=TABLE)
        for year, month in months:
            month_df = fetch_month(year, month)
            if month_df is None:
                logger.info("WASDE %04d-%02d: not published", year, month)
                continue
            tc.rows_fetched += month_df.height
            written += write_raw(SOURCE, month_df, table_name=TABLE)
        tc.rows_written = written
        return written

