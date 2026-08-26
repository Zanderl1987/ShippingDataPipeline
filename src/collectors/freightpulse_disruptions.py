"""Collect supply chain disruption alerts from FreightPulse.

FreightPulse (freightpulsehq.com) exposes a free REST API with no key required.
GET /api/v1/disruptions returns:

- data.active_alerts: count of active disruptions
- data.alerts[]: each with id, type, severity, title, description,
  affected_regions, affected_routes, impact (transit_delay, rate_increase,
  capacity_reduction), started_at, status
- data.resolved_recently[]: recently resolved disruptions
- data.risk_forecast: next-7-days risk level and factors

Each alert is stored as one row in supply_chain_disruptions.  Resolved
disruptions are also captured (status='resolved') to build a historical
record of disruption events.

Rate limit: 100 calls/month (free tier, no key).
Docs: https://freightpulsehq.com/docs
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

SOURCE = "freightpulse_disruptions"
API_URL = "https://freightpulsehq.com/api/v1/disruptions"


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


def _safe_int(val: Any) -> int | None:
    if val is None:
        return None
    try:
        return int(val)
    except (ValueError, TypeError):
        return None


def _parse_ts(val: Any) -> str | None:
    """Parse an ISO8601 timestamp, return the string or None."""
    if not val:
        return None
    return str(val)[:19]


def _parse_alert(alert: dict[str, Any]) -> dict[str, Any]:
    """Flatten a FreightPulse disruption alert into a row dict."""
    impact = alert.get("impact", {}) or {}
    affected_routes = alert.get("affected_routes", []) or []
    affected_regions = alert.get("affected_regions", []) or []

    return {
        "disruption_id": alert.get("id", ""),
        "disruption_type": alert.get("type", ""),
        "severity": alert.get("severity", ""),
        "title": alert.get("title", ""),
        "description": alert.get("description", ""),
        "affected_regions": json.dumps(affected_regions),
        "affected_routes": json.dumps(affected_routes),
        "transit_delay_days": _safe_int(impact.get("transit_delay_days")),
        "rate_increase_pct": _safe_float(impact.get("rate_increase_percent")),
        "capacity_reduction_pct": _safe_float(
            impact.get("capacity_reduction_percent")
        ),
        "started_at": _parse_ts(alert.get("started_at")),
        "expected_resolution": _parse_ts(alert.get("expected_resolution")),
        "status": alert.get("status", "unknown"),
    }


def collect_disruptions(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect supply chain disruption alerts from FreightPulse.

    Fetches GET /api/v1/disruptions (no auth required) and writes one
    row per alert (active + recently resolved) into
    supply_chain_disruptions.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        resp = get_with_retry(API_URL, timeout=30, source=SOURCE)
        payload: dict[str, Any] = resp.json()

        if not payload.get("success"):
            logger.warning("FreightPulse disruptions API returned success=false")
            return 0

        data = payload.get("data", {})
        snapshot_date = _parse_snapshot_date(data)

        alerts = data.get("alerts", []) or []
        resolved = data.get("resolved_recently", []) or []

        records: list[dict[str, Any]] = []
        for alert in alerts:
            rec = _parse_alert(alert)
            rec["snapshot_date"] = snapshot_date
            records.append(rec)

        for alert in resolved:
            rec = _parse_alert(alert)
            rec["snapshot_date"] = snapshot_date
            rec["status"] = "resolved"
            records.append(rec)

        if not records:
            logger.info("No FreightPulse disruption records parsed")
            return 0

        df = pl.DataFrame(records)
        df = df.with_columns(
            pl.lit(SOURCE).alias("source"),
            pl.lit(date.today()).alias("partition_date"),
        )

        tc.rows_fetched = df.height
        logger.info("Writing %d FreightPulse disruption records", df.height)
        count = write_raw(SOURCE, df, table_name="supply_chain_disruptions")
        tc.rows_written = count
        return count
