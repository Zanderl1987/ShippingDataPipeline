"""Collect container freight rates and indices from FreightPulse.

FreightPulse (freightpulsehq.com) exposes a free REST API with no key required.
GET /api/v1/freight-rates returns a single payload with:

- data.ocean.container_rates[]: per-route rates for 20ft/40ft/40hc, transit days,
  trend, week-over-week change
- data.ocean.indices: fbx_global, scfi, wci (composite index values)
- data.trucking: US average and spot rates by lane
- data.air: rates per kg by lane

This collector targets the freight_rates table.  Each container_rate entry is
exploded into one row per equipment type (20ft, 40ft, 40hc).  The composite
indices (fbx_global, scwi, wci) are stored as separate index-only rows.

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

SOURCE = "freightpulse_rates"
API_URL = "https://freightpulsehq.com/api/v1/freight-rates"

# Equipment type mapping: field name → container_type value
_EQUIP_MAP = {
    "rate_20ft": "20ft",
    "rate_40ft": "40ft",
    "rate_40hc": "40hc",
}

# Route name → (origin, destination) split
_ROUTE_SPLIT: dict[str, tuple[str, str]] = {}


def _split_route(route_name: str) -> tuple[str, str]:
    """Split a route string like 'Shanghai → Los Angeles' into (origin, dest)."""
    if route_name in _ROUTE_SPLIT:
        return _ROUTE_SPLIT[route_name]
    for sep in ("→", "->", " to "):
        if sep in route_name:
            parts = [p.strip() for p in route_name.split(sep, 1)]
            if len(parts) == 2:
                _ROUTE_SPLIT[route_name] = (parts[0], parts[1])
                return (parts[0], parts[1])
    return (route_name, "")


def _parse_snapshot_date(data: dict[str, Any]) -> date:
    """Extract the snapshot date from the FreightPulse payload timestamp."""
    ts = data.get("timestamp", "")
    if ts:
        try:
            return date.fromisoformat(ts[:10])
        except (ValueError, TypeError):
            pass
    return date.today()


def _parse_container_rates(
    container_rates: list[dict[str, Any]],
    snapshot_date: date,
) -> list[dict[str, Any]]:
    """Explode container_rates into one row per equipment type.

    Each FreightPulse container_rate entry has route_code, route, rate_20ft,
    rate_40ft, rate_40hc, transit_days, trend, change_week.  We emit 3 rows
    (20ft, 40ft, 40hc) per route, each with the rate under `rate_usd`.
    """
    records: list[dict[str, Any]] = []
    for cr in container_rates:
        route_code = cr.get("route_code", "")
        route_name = cr.get("route", "")
        origin, destination = _split_route(route_name)
        for field, ctype in _EQUIP_MAP.items():
            rate = cr.get(field)
            if rate is None:
                continue
            try:
                rate_val = float(rate)
            except (ValueError, TypeError):
                continue
            records.append({
                "route_code": route_code,
                "route_name": route_name,
                "origin": origin,
                "destination": destination,
                "container_type": ctype,
                "rate_usd": rate_val,
                "rate_date": snapshot_date,
            })
    return records


def _parse_indices(
    indices: dict[str, Any],
    snapshot_date: date,
) -> list[dict[str, Any]]:
    """Parse composite freight indices into index-only rows.

    Indices use the index name as route_code (e.g. 'fbx_global') and the
    composite value as rate_usd.  container_type is set to 'index' to
    distinguish from per-route container rates.
    """
    index_map = {
        "fbx_global": "FBX Global Container Index",
        "scfi": "Shanghai Containerized Freight Index",
        "wci": "World Container Index",
    }
    records: list[dict[str, Any]] = []
    for key, name in index_map.items():
        val = indices.get(key)
        if val is None:
            continue
        try:
            rate_val = float(val)
        except (ValueError, TypeError):
            continue
        records.append({
            "route_code": key,
            "route_name": name,
            "origin": "",
            "destination": "",
            "container_type": "index",
            "rate_usd": rate_val,
            "rate_date": snapshot_date,
        })
    return records


def collect_freight_rates(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect container freight rates from FreightPulse.

    Fetches GET /api/v1/freight-rates (no auth required) and writes
    per-route container rates + composite indices into freight_rates.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        resp = get_with_retry(API_URL, timeout=30, source=SOURCE)
        payload: dict[str, Any] = resp.json()

        if not payload.get("success"):
            logger.warning("FreightPulse freight rates API returned success=false")
            return 0

        data = payload.get("data", {})
        inner = data.get("data", {})
        ocean = inner.get("ocean", {})

        container_rates = ocean.get("container_rates", [])
        indices = ocean.get("indices", {})

        snapshot_date = _parse_snapshot_date(data)
        records = _parse_container_rates(container_rates, snapshot_date)
        records.extend(_parse_indices(indices, snapshot_date))

        if not records:
            logger.info("No FreightPulse freight rate records parsed")
            return 0

        df = pl.DataFrame(records)
        df = df.with_columns(
            pl.lit(SOURCE).alias("source"),
            pl.lit(date.today()).alias("partition_date"),
        )
        df = df.select(
            "route_code", "route_name", "origin", "destination",
            "container_type", "rate_usd", "rate_date",
            "source", "partition_date",
        )

        tc.rows_fetched = df.height
        logger.info("Writing %d FreightPulse freight rate records", df.height)
        count = write_raw(SOURCE, df, table_name="freight_rates")
        tc.rows_written = count
        return count
