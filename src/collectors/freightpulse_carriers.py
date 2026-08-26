"""Collect carrier performance data from FreightPulse.

FreightPulse provides ocean, trucking, and air carrier performance metrics
including reliability scores, on-time performance, fleet sizes, and market
share. No auth required, same 100 calls/month budget as other FreightPulse
endpoints.

Source: https://freightpulsehq.com/api/v1/carriers
Response shape:
    {
      "success": true,
      "data": {
        "timestamp": "2026-08-26T10:10:11.423Z",
        "source": "Carrier Performance Database",
        "data": {
          "ocean": [
            {
              "name": "Maersk", "code": "MAEU", "country": "DK",
              "fleet_teu": 4300000, "vessels": 708,
              "reliability_score": 72, "market_share": 17.1,
              "on_time_performance": 74, "avg_transit_delay_hours": 22,
              "customer_rating": "3.6", "updated_at": "..."
            }, ...
          ],
          "trucking": [
            {
              "name": "J.B. Hunt", "code": "JBHT", "country": "US",
              "fleet_size": 12000, "reliability_score": 85,
              "coverage": "National", "on_time_performance": 83,
              "avg_transit_delay_hours": 8, "customer_rating": "4.3"
            }, ...
          ],
          "air": [
            {
              "name": "FedEx", "code": "FDX", "country": "US",
              "fleet_size": 680, "reliability_score": 89,
              "market_share": 24.5, "on_time_performance": 90,
              "avg_transit_delay_hours": 3, "customer_rating": "4.5"
            }, ...
          ]
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

API_URL = "https://freightpulsehq.com/api/v1/carriers"
SOURCE = "freightpulse_carriers"


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


def _parse_carriers(data: dict[str, Any]) -> pl.DataFrame:
    """Parse ocean, trucking, and air carriers into a unified DataFrame."""
    payload = data.get("data") if isinstance(data.get("data"), dict) else {}
    inner = payload.get("data") if isinstance(payload.get("data"), dict) else {}

    snapshot_date = date.today()
    snapshot_ts = payload.get("timestamp")
    if isinstance(snapshot_ts, str) and snapshot_ts[:10]:
        try:
            snapshot_date = date.fromisoformat(snapshot_ts[:10])
        except ValueError:
            pass

    records: list[dict[str, Any]] = []

    for carrier in inner.get("ocean", []):
        if not isinstance(carrier, dict) or not carrier.get("code"):
            continue
        records.append({
            "snapshot_date": snapshot_date,
            "carrier_name": carrier.get("name"),
            "carrier_code": carrier.get("code"),
            "carrier_type": "ocean",
            "country": carrier.get("country"),
            "fleet_size": _safe_int(carrier.get("fleet_teu")),
            "fleet_size_unit": "teu",
            "vehicle_count": _safe_int(carrier.get("vessels")),
            "reliability_score": _safe_float(carrier.get("reliability_score")),
            "market_share_pct": _safe_float(carrier.get("market_share")),
            "on_time_performance_pct": _safe_float(carrier.get("on_time_performance")),
            "avg_delay_hours": _safe_float(carrier.get("avg_transit_delay_hours")),
            "customer_rating": _safe_float(carrier.get("customer_rating")),
        })

    for carrier in inner.get("trucking", []):
        if not isinstance(carrier, dict) or not carrier.get("code"):
            continue
        records.append({
            "snapshot_date": snapshot_date,
            "carrier_name": carrier.get("name"),
            "carrier_code": carrier.get("code"),
            "carrier_type": "trucking",
            "country": carrier.get("country"),
            "fleet_size": _safe_int(carrier.get("fleet_size")),
            "fleet_size_unit": "vehicles",
            "vehicle_count": None,
            "reliability_score": _safe_float(carrier.get("reliability_score")),
            "market_share_pct": None,
            "on_time_performance_pct": _safe_float(carrier.get("on_time_performance")),
            "avg_delay_hours": _safe_float(carrier.get("avg_transit_delay_hours")),
            "customer_rating": _safe_float(carrier.get("customer_rating")),
        })

    for carrier in inner.get("air", []):
        if not isinstance(carrier, dict) or not carrier.get("code"):
            continue
        records.append({
            "snapshot_date": snapshot_date,
            "carrier_name": carrier.get("name"),
            "carrier_code": carrier.get("code"),
            "carrier_type": "air",
            "country": carrier.get("country"),
            "fleet_size": _safe_int(carrier.get("fleet_size")),
            "fleet_size_unit": "aircraft",
            "vehicle_count": None,
            "reliability_score": _safe_float(carrier.get("reliability_score")),
            "market_share_pct": _safe_float(carrier.get("market_share")),
            "on_time_performance_pct": _safe_float(carrier.get("on_time_performance")),
            "avg_delay_hours": _safe_float(carrier.get("avg_transit_delay_hours")),
            "customer_rating": _safe_float(carrier.get("customer_rating")),
        })

    if not records:
        return pl.DataFrame()

    return pl.DataFrame(records)


def collect_carriers(tracker: SourceTracker | None = None) -> int:
    """Collect FreightPulse carrier performance data (ocean, trucking, air).

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        resp = get_with_retry(API_URL, timeout=30, source=SOURCE)
        data: dict[str, Any] = resp.json()
        df = _parse_carriers(data)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No FreightPulse carrier data returned")
            return 0

        logger.info("Writing %d FreightPulse carrier records", df.height)
        count = write_raw(SOURCE, df, table_name="carriers")
        tc.rows_written = count
        return count
