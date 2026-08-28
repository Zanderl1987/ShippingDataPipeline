"""Collect WTI/Brent crude oil price benchmarks from FRED (St. Louis Fed).

FRED's ``series/observations`` endpoint requires a free API key
(fredaccount.stlouisfed.org). Confirmed live without a key: the endpoint
400s with ``"Variable api_key is not set"`` (not a 404, so the route itself
is real), and both series IDs below resolve to real public series pages --
also checked keylessly.

WTI and Brent are two separate FRED series. Observations are merged into
one row per date with both ``wti_usd``/``brent_usd`` columns populated
before writing once, matching ``oil_prices``' ``(price_date, source)``
dedup key -- two separate single-column writes would each overwrite the
other's row for the same key (same pattern as ``oilpriceapi.py``'s
``_parse_oil_prices``).

CAUTION: like ``census_trade.py``, this is built against FRED's documented
metadata/error format (live-confirmed keylessly) but the authenticated
``observations`` response body has NOT been seen -- ``FRED_API_KEY`` was
not yet registered as of this writing. FRED's JSON shape has been stable
and documented for over a decade, but verify against a real response the
first time a key is available.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://api.stlouisfed.org/fred/series/observations"
SOURCE = "fred"

# FRED series ID -> oil_prices column.
SERIES_TO_COLUMN = {
    "DCOILWTICO": "wti_usd",      # Crude Oil Prices: West Texas Intermediate, Daily
    "DCOILBRENTEU": "brent_usd",  # Crude Oil Prices: Brent - Europe, Daily
}

# FRED represents a missing daily observation (holiday/weekend) as this.
_MISSING_VALUE = "."


def _get_api_key() -> str:
    key = settings.fred_api_key
    if not key:
        raise ValueError("FRED_API_KEY not set in environment")
    return key


def fetch_series_observations(
    *,
    series_id: str,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    """Fetch daily observations for one FRED series.

    Returns:
        The raw ``observations`` list: dicts with (at least) ``date`` and
        ``value`` keys, value as a string (``"."`` means no observation).
    """
    params = {
        "series_id": series_id,
        "observation_start": start_date,
        "observation_end": end_date,
        "file_type": "json",
        "api_key": _get_api_key(),
    }
    logger.info("Fetching FRED series %s (%s to %s)", series_id, start_date, end_date)
    resp = requests.get(BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    payload: dict[str, Any] = resp.json()
    observations = payload.get("observations", [])
    return observations if isinstance(observations, list) else []


def parse_fred_oil_prices(series_observations: dict[str, list[dict[str, Any]]]) -> pl.DataFrame:
    """Merge multiple FRED series' observations into one row per date.

    Args:
        series_observations: series_id -> list of {"date": ..., "value": ...}.
    """
    by_date: dict[str, dict[str, Any]] = {}
    for series_id, observations in series_observations.items():
        column = SERIES_TO_COLUMN.get(series_id)
        if column is None:
            continue
        for obs in observations:
            raw_date = obs.get("date")
            raw_value = obs.get("value")
            if not raw_date or raw_value in (None, _MISSING_VALUE):
                continue
            try:
                value = float(raw_value)
            except (TypeError, ValueError):
                continue
            row = by_date.setdefault(raw_date, {"price_date": raw_date})
            row[column] = value

    if not by_date:
        return pl.DataFrame()

    df = pl.DataFrame(list(by_date.values())).with_columns(
        pl.col("price_date").str.to_date(),
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )
    return df


def collect_oil_prices(
    *,
    start_date: str | None = None,
    end_date: str | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect FRED WTI/Brent crude oil prices and write to oil_prices.

    Defaults to the last 30 days.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    if end_date is None:
        end_date = date.today().isoformat()
    if start_date is None:
        start_date = (date.today() - timedelta(days=30)).isoformat()

    with TimedCollector(tracker, SOURCE) as tc:
        series_observations = {
            series_id: fetch_series_observations(
                series_id=series_id, start_date=start_date, end_date=end_date,
            )
            for series_id in SERIES_TO_COLUMN
        }
        df = parse_fred_oil_prices(series_observations)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No FRED oil price data returned for %s to %s", start_date, end_date)
            return 0

        logger.info("Writing %d FRED oil price records", df.height)
        count = write_raw(SOURCE, df, table_name="oil_prices")
        tc.rows_written = count
        return count
