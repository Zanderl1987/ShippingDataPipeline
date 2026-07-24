from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://hormuzmonitor.com/api"
SOURCE = "hormuz_monitor"


def _get_api_key() -> str | None:
    """Get Hormuz Monitor API key (optional for free tier)."""
    return settings.hormuz_api_key


def get_risk() -> dict[str, Any]:
    """Get composite Hormuz risk index (free endpoint)."""
    url = f"{BASE_URL}/risk"
    headers: dict[str, str] = {}
    key = _get_api_key()
    if key:
        headers["X-API-Key"] = key

    logger.info("Fetching Hormuz Monitor risk index")
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_prices() -> dict[str, Any]:
    """Get oil benchmark prices and tanker rates (free, 15-min delay)."""
    url = f"{BASE_URL}/prices"
    headers: dict[str, str] = {}
    key = _get_api_key()
    if key:
        headers["X-API-Key"] = key

    logger.info("Fetching Hormuz Monitor oil prices")
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_crisis() -> dict[str, Any]:
    """Get current crisis status (free endpoint)."""
    url = f"{BASE_URL}/crisis"
    headers: dict[str, str] = {}
    key = _get_api_key()
    if key:
        headers["X-API-Key"] = key

    logger.info("Fetching Hormuz Monitor crisis status")
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_traffic() -> dict[str, Any]:
    """Get daily tanker transit counts (free endpoint)."""
    url = f"{BASE_URL}/traffic"
    headers: dict[str, str] = {}
    key = _get_api_key()
    if key:
        headers["X-API-Key"] = key

    logger.info("Fetching Hormuz Monitor traffic data")
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _parse_oil_prices(prices_data: dict[str, Any]) -> pl.DataFrame:
    """Parse Hormuz Monitor prices into oil_prices schema."""
    data = prices_data.get("data", prices_data)
    if not data:
        return pl.DataFrame()

    record = {
        "brent_usd": data.get("brent_usd"),
        "wti_usd": data.get("wti_usd"),
        "dubai_usd": data.get("dubai_usd"),
        "lng_jkm_mmbtu": data.get("lng_jkm_mmbtu"),
        "vlcc_td3c_ws": data.get("vlcc_td3c_ws"),
        "vlcc_td3c_tce_usd_day": data.get("vlcc_td3c_tce_usd_day"),
        "risk_premium_pct": data.get("risk_premium_pct"),
        "td_change_pct": data.get("td_change_pct"),
    }

    df = pl.DataFrame([record])

    for col in df.columns:
        if col in ["brent_usd", "wti_usd", "dubai_usd", "lng_jkm_mmbtu",
                    "vlcc_td3c_ws", "vlcc_td3c_tce_usd_day",
                    "risk_premium_pct", "td_change_pct"]:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("price_date"),
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def _parse_traffic(traffic_data: dict[str, Any]) -> pl.DataFrame:
    """Parse Hormuz Monitor traffic into chokepoint_transits schema."""
    data = traffic_data.get("data", traffic_data)
    if not data:
        return pl.DataFrame()

    record = {
        "transit_date": date.today().isoformat(),
        "chokepoint_id": "strait-of-hormuz",
        "chokepoint_name": "Strait of Hormuz",
        "n_container": 0,
        "n_dry_bulk": 0,
        "n_general_cargo": 0,
        "n_roro": 0,
        "n_tanker": data.get("tanker_count", 0),
        "n_cargo": 0,
        "n_total": data.get("transits_today", 0),
        "capacity_container": 0,
        "capacity_dry_bulk": 0,
        "capacity_general_cargo": 0,
        "capacity_roro": 0,
        "capacity_tanker": data.get("tanker_dwt", 0),
        "capacity_cargo": 0,
        "capacity": data.get("total_capacity", 0),
    }

    df = pl.DataFrame([record])
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_oil_prices(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect oil prices and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_prices()
        df = _parse_oil_prices(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Hormuz Monitor price data returned")
            return 0

        logger.info("Writing Hormuz Monitor oil prices")
        count = write_raw(SOURCE, df, table_name="oil_prices")
        tc.rows_written = count
        return count


def collect_traffic(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect Hormuz traffic data and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_traffic()
        df = _parse_traffic(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Hormuz Monitor traffic data returned")
            return 0

        logger.info("Writing Hormuz Monitor traffic data")
        count = write_raw(SOURCE, df, table_name="chokepoint_transits")
        tc.rows_written = count
        return count
