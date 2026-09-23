"""Collect FreightPulse port congestion data for 114 global ports.

FreightPulse provides a free REST API (no key required for basic calls,
100 calls/month free tier) with real-time port congestion metrics including
vessel counts, wait times, berth utilization, and container dwell times.

Source: https://freightpulsehq.com/api/v1/port-congestion
Response shape:
    {
      "success": true,
      "data": {
        "timestamp": "2026-08-26T09:24:24.058Z",
        "source": "Port Authorities + AIS Data",
        "total_ports": 114,
        "data": {
          "ports": [
            {
              "port": "Los Angeles",
              "port_code": "USLAX",
              "country": "US",
              "region": "North America",
              "lat": 33.74,
              "lon": -118.27,
              "capacity_teu": 9500000,
              "congestion_index": 45,
              "congestion_level": "moderate",
              "vessels_at_anchor": 8,
              "vessels_at_berth": 38,
              "avg_wait_time_hours": 18,
              "avg_berth_time_hours": 59,
              "container_dwell_days": 3,
              "trend": "stable",
              "change_week": 0,
              "updated_at": "2026-08-26T09:24:24.058Z"
            },
            ...
          ],
          "global_summary": { ... }
        }
      }
    }
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

API_URL = "https://freightpulsehq.com/api/v1/port-congestion"
SOURCE = "freightpulse"


def fetch_port_congestion() -> dict[str, Any]:
    """Fetch all port congestion data from FreightPulse (no auth required)."""
    resp = get_with_retry(API_URL, timeout=30, source=SOURCE)
    data: dict[str, Any] = resp.json()
    total = data.get("data", {}).get("total_ports", "?")
    logger.debug("FreightPulse returned %s ports", total)
    return data


def _parse_ports(data: dict[str, Any]) -> pl.DataFrame:
    """Parse the FreightPulse response into per-port congestion rows."""
    raw = data.get("data")
    payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
    raw_inner = payload.get("data")
    inner: dict[str, Any] = raw_inner if isinstance(raw_inner, dict) else {}
    ports = inner.get("ports")
    if not isinstance(ports, list):
        logger.warning("FreightPulse: no ports array in response")
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    snapshot_ts = payload.get("timestamp")
    snapshot_date = date.today()
    if isinstance(snapshot_ts, str) and snapshot_ts[:10]:
        try:
            snapshot_date = date.fromisoformat(snapshot_ts[:10])
        except ValueError:
            pass

    for p in ports:
        if not isinstance(p, dict):
            continue
        port_code = p.get("port_code")
        if not port_code:
            continue
        records.append({
            "snapshot_date": snapshot_date,
            "port_code": str(port_code),
            "port_name": p.get("port"),
            "country": p.get("country"),
            "region": p.get("region"),
            "latitude": _safe_float(p.get("lat")),
            "longitude": _safe_float(p.get("lon")),
            "capacity_teu": _safe_float(p.get("capacity_teu")),
            "congestion_index": _safe_float(p.get("congestion_index")),
            "congestion_level": p.get("congestion_level"),
            "vessels_at_anchor": _safe_int(p.get("vessels_at_anchor")),
            "vessels_at_berth": _safe_int(p.get("vessels_at_berth")),
            "avg_wait_time_hours": _safe_float(p.get("avg_wait_time_hours")),
            "avg_berth_time_hours": _safe_float(p.get("avg_berth_time_hours")),
            "container_dwell_days": _safe_float(p.get("container_dwell_days")),
            "trend": p.get("trend"),
            "change_week": _safe_int(p.get("change_week")),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.lit(SOURCE).alias("source"),
        pl.lit(date.today()).alias("partition_date"),
    )
    return df


def _safe_float(v: Any) -> float | None:
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_int(v: Any) -> int | None:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def collect_port_congestion(tracker: SourceTracker | None = None) -> int:
    """Collect FreightPulse port congestion data for all global ports.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        data = fetch_port_congestion()
        df = _parse_ports(data)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No FreightPulse port congestion data returned")
            return 0

        logger.info("Writing %d FreightPulse port congestion records", df.height)
        count = write_raw(SOURCE, df, table_name="port_congestion")
        tc.rows_written = count
        return count
