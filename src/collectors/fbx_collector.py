"""Collect container freight rates from Drewry World Container Index (FBX proxy)."""
from __future__ import annotations

import logging
import time
from datetime import date, datetime
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

DREWRY_WCI_URL = "https://www.drewry.co.uk/api/supply-chain-advisors/supply-chain-expertise/world-container-index"
SOURCE = "fbx_wci"

FALLBACK_INDEX_URL = "https://fbx.freightos.com/wp-json/fbx/v1/latest"
FALLBACK_API_URL = "https://fbx.freightos.com/wp-json/fbx/v1/history"

REQUEST_DELAY_SECONDS = 3


def fetch_drewry_wci() -> dict[str, Any]:
    """Fetch the latest Drewry World Container Index data."""
    resp = get_with_retry(
        DREWRY_WCI_URL,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def fetch_fbx_latest() -> dict[str, Any]:
    """Fetch the latest FBX index value from the public endpoint."""
    resp = get_with_retry(
        FALLBACK_INDEX_URL,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def fetch_fbx_history() -> dict[str, Any]:
    """Fetch historical FBX data if available publicly."""
    resp = get_with_retry(
        FALLBACK_API_URL,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def _parse_drewry(data: dict[str, Any]) -> pl.DataFrame:
    """Parse Drewry WCI response into a DataFrame."""
    records = data.get("data", data.get("results", []))
    if not records:
        if isinstance(data, dict) and "composite_index" in data:
            records = [data]
    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
        pl.lit("drewry_wci").alias("rate_type"),
    )
    return df


def _parse_fbx(data: dict[str, Any], rate_type: str = "fbx") -> pl.DataFrame:
    """Parse FBX response into a DataFrame."""
    records = data.get("rates", data.get("data", data.get("results", [])))
    if not records and isinstance(data, dict):
        records = [data]
    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
        pl.lit(rate_type).alias("rate_type"),
    )
    return df


def collect_data(tracker: SourceTracker | None = None) -> int:
    """Main entry point: fetch container freight rates and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        logger.info("Starting FBX / freight rate collection")
        frames: list[pl.DataFrame] = []

        try:
            raw = fetch_drewry_wci()
            df = _parse_drewry(raw)
            frames.append(df)
            logger.info("Fetched Drewry WCI: %d rows", df.height)
        except Exception:
            logger.exception("Drewry WCI fetch failed, trying FBX fallback")

        time.sleep(REQUEST_DELAY_SECONDS)

        try:
            raw = fetch_fbx_latest()
            df = _parse_fbx(raw, rate_type="fbx_index")
            frames.append(df)
            logger.info("Fetched FBX index: %d rows", df.height)
        except Exception:
            logger.exception("FBX index fetch failed")

        if not frames:
            logger.warning("No freight rate data collected")
            tc.rows_fetched = 0
            return 0

        combined = pl.concat(frames)
        tc.rows_fetched = combined.height

        logger.info("Writing %d freight rate records", combined.height)
        count = write_raw(SOURCE, combined, table_name="freight_rates")
        tc.rows_written = count
        return count
