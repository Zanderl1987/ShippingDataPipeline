"""Collect chokepoint risk data from Eagle Intelligence."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://eagleintelmari.com"
SOURCE = "eagle_intelligence"


def get_chokepoint_status() -> dict[str, Any]:
    """Get live risk status for all 6 monitored maritime chokepoints.

    No authentication required. Attribution required (CC BY 4.0).
    Rate limit: 1 req/min (fair use).

    Returns:
        Raw API response dict with chokepoints list.
    """
    url = f"{BASE_URL}/api/chokepoint-status"
    logger.info("Fetching Eagle Intelligence chokepoint status")
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_hormuz_status() -> dict[str, Any]:
    """Get live risk status for the Strait of Hormuz specifically.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/api/hormuz-status"
    logger.info("Fetching Eagle Intelligence Hormuz status")
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _parse_chokepoint_status(data: dict[str, Any]) -> pl.DataFrame:
    """Parse chokepoint status data into a DataFrame."""
    chokepoints = data.get("chokepoints", [])
    if not chokepoints:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for cp in chokepoints:
        records.append({
            "chokepoint_id": cp.get("chokepoint", ""),
            "chokepoint_name": cp.get("name", ""),
            "status": cp.get("status", ""),
            "signals_last_24h": cp.get("signalsLast24h", 0),
            "high_alerts_last_24h": cp.get("highAlertsLast24h", 0),
            "signals_last_7d": cp.get("signalsLast7d", 0),
            "latest_high_headline": cp.get("latestHighHeadline"),
            "crisis_day": cp.get("crisisDay"),
            "situation_url": cp.get("situationUrl"),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def _parse_hormuz_status(data: dict[str, Any]) -> pl.DataFrame:
    """Parse Hormuz-specific status into a DataFrame."""
    if not data or "status" not in data:
        return pl.DataFrame()

    record = {
        "chokepoint_id": data.get("situation", "strait-of-hormuz"),
        "chokepoint_name": data.get("name", "Strait of Hormuz Crisis"),
        "status": data.get("status", ""),
        "signals_last_24h": data.get("signalsLast24h", 0),
        "high_alerts_last_24h": data.get("highAlertsLast24h", 0),
        "signals_last_7d": data.get("signalsLast7d", 0),
        "latest_high_headline": data.get("latestHighHeadline"),
        "crisis_day": data.get("crisisDay"),
        "situation_url": data.get("situationUrl"),
    }

    df = pl.DataFrame([record])

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_chokepoint_status(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect all chokepoint statuses and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_chokepoint_status()
        df = _parse_chokepoint_status(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No chokepoint data returned from Eagle Intelligence")
            return 0

        logger.info(
            "Writing %d chokepoint statuses from Eagle Intelligence", df.height
        )
        count = write_raw(SOURCE, df, table_name="chokepoint_status")
        tc.rows_written = count
        return count


def collect_hormuz_status(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect Hormuz-specific status and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_hormuz_status()
        df = _parse_hormuz_status(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Hormuz data returned from Eagle Intelligence")
            return 0

        logger.info("Writing Hormuz status from Eagle Intelligence")
        count = write_raw(SOURCE, df, table_name="chokepoint_status")
        tc.rows_written = count
        return count
