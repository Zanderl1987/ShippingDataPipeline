"""Collect oil price benchmarks and (when available) freight indices from OilPriceAPI.

OilPriceAPI (api.oilpriceapi.com) provides timestamped energy prices. Auth is
`Authorization: Token <key>` (env `OILPRICEAPI_KEY`). The free tier allows 200
requests/month after a 7-day trial, so collection is split into two jobs:

- oil prices (BRENT/WTI/DUBAI) -> oil_prices, scheduled daily (3 reqs/run)
- freight indices (BDI/BCI/SCFI/WCI) -> freight_rates, scheduled weekly

Freight indices are plan-gated and may not be present on a free key; the
collector tolerates their absence rather than failing the run.

Docs: https://docs.oilpriceapi.com
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://api.oilpriceapi.com"
LATEST_URL = f"{BASE_URL}/v1/prices/latest"

SOURCE = "oilpriceapi"

# Verified available in the free catalog; map 1:1 to oil_prices columns.
OIL_BENCHMARKS = {
    "BRENT_CRUDE_USD": "brent_usd",
    "WTI_USD": "wti_usd",
    "DUBAI_CRUDE_USD": "dubai_usd",
}

# Plan-gated freight indices. Route/container columns are null because these
# are composite indices, not per-route container rates.
FREIGHT_INDICES = {
    "BALTIC_DRY_INDEX": "Baltic Dry Index",
    "BALTIC_CAPESIZE_INDEX": "Baltic Capesize Index",
    "SCFI": "Shanghai Containerized Freight Index",
    "DREWRY_WCI_USD": "Drewry World Container Index",
}


def _get_auth_headers() -> dict[str, str]:
    key = getattr(settings, "oilpriceapi_api_key", None)
    if not key:
        raise RuntimeError("OILPRICEAPI_API_KEY is not set in configuration")
    return {"Authorization": f"Token {key}"}


def _fetch_prices(codes: list[str]) -> list[dict[str, Any]]:
    """Fetch latest prices for the given codes.

    Tries one batched request (comma-joined by_code); falls back to one
    request per code if the API rejects the batch.
    """
    headers = _get_auth_headers()
    for attempt in (codes, *([c] for c in codes)):
        try:
            resp = get_with_retry(
                LATEST_URL,
                params={"by_code": ",".join(attempt)},
                headers=headers,
                timeout=30,
                source=SOURCE,
            )
            data: dict[str, Any] = resp.json()
            prices = _extract_prices(data)
            if prices:
                return prices
        except Exception:
            logger.warning("OilPriceAPI batch request failed for %s", attempt)
            continue
    return []


def _extract_prices(data: dict[str, Any]) -> list[dict[str, Any]]:
    """Normalize the response into a list of price records.

    Handles both the single-price shape (data itself carries `code`) and the
    list shape (`data.prices`), and tolerates a 404 for unknown/plan-gated
    codes.
    """
    payload = data.get("data", {})
    if not isinstance(payload, dict):
        return []
    if isinstance(payload.get("code"), str):
        return [payload]
    prices = payload.get("prices")
    if isinstance(prices, list):
        return [p for p in prices if isinstance(p, dict)]
    return []


def _price_to_date(p: dict[str, Any]) -> date | None:
    updated = p.get("updated_at") or p.get("updatedAt")
    if not updated:
        return date.today()
    try:
        return date.fromisoformat(str(updated)[:10])
    except ValueError:
        return date.today()


def _parse_oil_prices(prices: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse benchmark prices into one row per price_date (oil_prices)."""
    by_date: dict[date, dict[str, Any]] = {}
    for p in prices:
        code = p.get("code")
        price = p.get("price")
        if not isinstance(code, str) or price is None:
            continue
        col = OIL_BENCHMARKS.get(code)
        if not col:
            continue
        d = _price_to_date(p)
        if d is None:
            d = date.today()
        row = by_date.setdefault(d, {})
        row["price_date"] = d
        row[col] = price

    if not by_date:
        return pl.DataFrame()

    df = pl.DataFrame(list(by_date.values()))
    df = df.with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )
    return df


def _parse_freight(prices: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse freight indices into per-index rows (freight_rates)."""
    records: list[dict[str, Any]] = []
    for p in prices:
        code = p.get("code")
        price = p.get("price")
        if not isinstance(code, str) or price is None:
            continue
        name = FREIGHT_INDICES.get(code)
        if not name:
            continue
        records.append({
            "route_code": code,
            "route_name": name,
            "rate_usd": price,
            "rate_date": _price_to_date(p) or date.today(),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    df = df.with_columns(pl.lit(SOURCE).alias("source"))
    return df


def collect_oil_prices(tracker: SourceTracker | None = None) -> int:
    """Collect latest benchmark oil prices and write to oil_prices.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, "oilpriceapi_oil") as tc:
        if not getattr(settings, "oilpriceapi_api_key", None):
            logger.warning("OILPRICEAPI_API_KEY not set, skipping oil prices")
            return 0
        prices = _fetch_prices(list(OIL_BENCHMARKS))
        df = _parse_oil_prices(prices)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No OilPriceAPI oil price data returned")
            return 0

        logger.info("Writing %d OilPriceAPI oil price records", df.height)
        count = write_raw(SOURCE, df, table_name="oil_prices")
        tc.rows_written = count
        return count


def collect_freight_indices(tracker: SourceTracker | None = None) -> int:
    """Collect freight indices (when available) into freight_rates.

    Indices are plan-gated; a missing/invalid code yields no rows, which is
    tolerated (freight_rates is also fed by fbx_collector).

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, "oilpriceapi_freight") as tc:
        if not getattr(settings, "oilpriceapi_api_key", None):
            logger.warning("OILPRICEAPI_API_KEY not set, skipping freight indices")
            return 0
        prices = _fetch_prices(list(FREIGHT_INDICES))
        df = _parse_freight(prices)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.info("No OilPriceAPI freight indices returned (may be plan-gated)")
            return 0

        logger.info("Writing %d OilPriceAPI freight index records", df.height)
        count = write_raw(SOURCE, df, table_name="freight_rates")
        tc.rows_written = count
        return count
