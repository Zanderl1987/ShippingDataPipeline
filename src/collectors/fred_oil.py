"""Collect WTI/Brent crude oil price benchmarks from FRED (St. Louis Fed).

Uses FRED's chart download (``fredgraph.csv``), which needs no API key:
one CSV with a date column and one column per series, "" for a day with no
price. Both series come back in one request, so each row already has both
``wti_usd`` and ``brent_usd`` -- one write per date, matching ``oil_prices``'
``(price_date, source)`` dedup key. The JSON ``series/observations`` API needs
a key, and no ``FRED_API_KEY`` secret exists in CI, so this collector never
ran there before 2026-09-25.

The whole history (WTI from 1986, Brent from 1987) is ~230 KB. An empty table
is filled with all of it when bulk backfill is allowed (CI); otherwise each
run re-fetches the last ``LOOKBACK_DAYS``.
"""
from __future__ import annotations

import io
import logging
from datetime import date, timedelta

import duckdb
import polars as pl

from src.collectors.http_utils import get_with_retry
from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import get_connection, write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv"
SOURCE = "fred"
#: Registered name in collect_all; the tracker row must use it.
TRACKER_NAME = "fred_oil"
LOOKBACK_DAYS = 30

# FRED series ID -> oil_prices column.
SERIES_TO_COLUMN = {
    "DCOILWTICO": "wti_usd",      # Crude Oil Prices: West Texas Intermediate, Daily
    "DCOILBRENTEU": "brent_usd",  # Crude Oil Prices: Brent - Europe, Daily
}


def fetch_prices_csv(start_date: str | None = None, end_date: str | None = None) -> str:
    """Download both series as one CSV; no dates means the full history."""
    params = {"id": ",".join(SERIES_TO_COLUMN)}
    if start_date:
        params["cosd"] = start_date
    if end_date:
        params["coed"] = end_date
    logger.info("Fetching FRED oil prices (%s to %s)", start_date or "start", end_date or "now")
    resp = get_with_retry(BASE_URL, params=params, timeout=60)
    return str(resp.text)


def parse_fred_csv(text: str) -> pl.DataFrame:
    """One row per date with at least one price; unknown columns are ignored."""
    if not text.strip():
        return pl.DataFrame()
    raw = pl.read_csv(io.StringIO(text), infer_schema=False)
    date_col = raw.columns[0]  # "observation_date" (older exports: "DATE")
    present = {s: c for s, c in SERIES_TO_COLUMN.items() if s in raw.columns}
    if not present:
        return pl.DataFrame()
    df = raw.select(
        pl.col(date_col).str.to_date().alias("price_date"),
        # "" or "." marks a day with no price.
        *[pl.col(s).cast(pl.Float64, strict=False).alias(c) for s, c in present.items()],
    ).filter(pl.any_horizontal(pl.col(list(present.values())).is_not_null()))
    return df.with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )


def _has_prices() -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT count(*) FROM oil_prices WHERE source = ?", [SOURCE]
        ).fetchone()
    except duckdb.CatalogException:
        return False
    finally:
        conn.close()
    return bool(row and row[0])


def collect_oil_prices(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    tracker: SourceTracker | None = None,
    bulk_backfill: bool | None = None,
) -> int:
    """Collect FRED WTI/Brent crude oil prices and write to oil_prices.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()
    if bulk_backfill is None:
        bulk_backfill = settings.allow_bulk_backfill

    if start_date is None and not (bulk_backfill and not _has_prices()):
        start_date = (date.today() - timedelta(days=LOOKBACK_DAYS)).isoformat()

    with TimedCollector(tracker, TRACKER_NAME) as tc:
        df = parse_fred_csv(fetch_prices_csv(start_date, end_date))
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No FRED oil price data returned since %s", start_date)
            return 0

        logger.info("Writing %d FRED oil price records", df.height)
        count = write_raw(SOURCE, df, table_name="oil_prices")
        tc.rows_written = count
        return count
