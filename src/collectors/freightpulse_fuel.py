"""Collect marine bunker and road diesel fuel prices from FreightPulse.

FreightPulse (freightpulsehq.com) exposes a free REST API with no key required.
GET /api/v1/fuel-prices returns:

- data.diesel: US national average by region
- data.gasoline: retail grades (regular/midgrade/premium)
- data.bunker_fuel: port-level bunker prices (Rotterdam, Singapore, Houston)
- data.historical: 30d/90d averages, YoY change

This collector targets a dedicated `fuel_prices` table (not oil_prices, which
tracks crude benchmarks).  One row per (source) snapshot with all fuel
categories.

Rate limit: 100 calls/month (free tier, no key).
Docs: https://freightpulsehq.com/docs
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

SOURCE = "freightpulse_fuel"
API_URL = "https://freightpulsehq.com/api/v1/fuel-prices"


def _parse_snapshot_date(data: dict[str, Any]) -> date:
    ts = data.get("timestamp", "")
    if ts:
        try:
            return date.fromisoformat(ts[:10])
        except (ValueError, TypeError):
            pass
    return date.today()


def _safe_float(val: Any) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _parse_fuel_prices(data: dict[str, Any]) -> dict[str, Any]:
    """Flatten the nested fuel-prices payload into a single row dict.

    ``data`` is the outer payload dict containing ``data["data"]`` with the
    diesel/gasoline/bunker fuel keys, and ``data["historical"]`` at the top.
    """
    inner = data.get("data", {})
    diesel = inner.get("diesel", {})
    gasoline = inner.get("gasoline", {})
    bunker = inner.get("bunker_fuel", {})
    hist = data.get("historical", {})
    regions = diesel.get("regions", {})
    grades = gasoline.get("grades", {})

    return {
        "diesel_national_avg": _safe_float(diesel.get("national_average")),
        "diesel_change_week": _safe_float(diesel.get("change_week")),
        "diesel_east_coast": _safe_float(regions.get("east_coast")),
        "diesel_midwest": _safe_float(regions.get("midwest")),
        "diesel_gulf_coast": _safe_float(regions.get("gulf_coast")),
        "diesel_rocky_mountain": _safe_float(regions.get("rocky_mountain")),
        "diesel_west_coast": _safe_float(regions.get("west_coast")),
        "diesel_california": _safe_float(regions.get("california")),
        "gasoline_regular": _safe_float(grades.get("regular")),
        "gasoline_midgrade": _safe_float(grades.get("midgrade")),
        "gasoline_premium": _safe_float(grades.get("premium")),
        "gasoline_national_avg": _safe_float(gasoline.get("national_average")),
        "bunker_rotterdam": _safe_float(bunker.get("rotterdam")),
        "bunker_singapore": _safe_float(bunker.get("singapore")),
        "bunker_houston": _safe_float(bunker.get("houston")),
        "diesel_30d_avg": _safe_float(hist.get("diesel_30d_avg")),
        "diesel_90d_avg": _safe_float(hist.get("diesel_90d_avg")),
        "diesel_yoy_change": _safe_float(hist.get("diesel_yoy_change")),
    }


def collect_fuel_prices(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect marine bunker and road diesel prices from FreightPulse.

    Fetches GET /api/v1/fuel-prices (no auth required) and writes one
    row per snapshot into the fuel_prices table.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        resp = get_with_retry(API_URL, timeout=30, source=SOURCE)
        payload: dict[str, Any] = resp.json()

        if not payload.get("success"):
            logger.warning("FreightPulse fuel prices API returned success=false")
            return 0

        data = payload.get("data", {})
        snapshot_date = _parse_snapshot_date(data)
        record = _parse_fuel_prices(data)

        if not any(v is not None for v in record.values()):
            logger.info("No FreightPulse fuel price data returned")
            return 0

        record["snapshot_date"] = snapshot_date
        df = pl.DataFrame([record])
        df = df.with_columns(
            pl.lit(SOURCE).alias("source"),
            pl.lit(date.today()).alias("partition_date"),
        )

        tc.rows_fetched = df.height
        logger.info("Writing %d FreightPulse fuel price records", df.height)
        count = write_raw(SOURCE, df, table_name="fuel_prices")
        tc.rows_written = count
        return count
