"""Collect vessel call data from Port of Barcelona OpenInfoAPI."""
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

BASE_URL = "https://portdebarcelona.cat"
API_BASE = f"{BASE_URL}/wp-json/openinfo/v1"
SOURCE = "barcelona_port"

VESSEL_CALLS_ENDPOINT = f"{API_BASE}/vessel-calls"
TRAFFIC_ENDPOINT = f"{API_BASE}/traffic"
CARGO_ENDPOINT = f"{API_BASE}/cargo"

REQUEST_DELAY_SECONDS = 2


def fetch_vessel_calls(page: int = 1, per_page: int = 100) -> dict[str, Any]:
    """Fetch vessel arrival/departure data from Barcelona port API."""
    params = {
        "page": str(page),
        "per_page": str(per_page),
    }
    resp = get_with_retry(
        VESSEL_CALLS_ENDPOINT,
        params=params,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def fetch_all_vessel_calls(max_pages: int = 20) -> list[dict[str, Any]]:
    """Paginate through vessel call data."""
    all_calls: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        logger.info("Fetching Barcelona vessel calls page %d", page)
        try:
            data = fetch_vessel_calls(page=page)
        except Exception:
            logger.exception("Failed to fetch Barcelona page %d", page)
            break

        records = data.get("data", data.get("results", []))
        if not records:
            logger.info("No more vessel calls at page %d", page)
            break

        all_calls.extend(records)
        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("Total Barcelona vessel calls fetched: %d", len(all_calls))
    return all_calls


def fetch_traffic_stats() -> dict[str, Any]:
    """Fetch port traffic statistics."""
    resp = get_with_retry(
        TRAFFIC_ENDPOINT,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def fetch_cargo_volumes() -> dict[str, Any]:
    """Fetch cargo volume data."""
    resp = get_with_retry(
        CARGO_ENDPOINT,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def _parse_vessel_calls(records: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse vessel call records into a DataFrame."""
    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    for col_name in ("imo", "mmsi", "vessel_name", "flag", "port"):
        if col_name in df.columns:
            df = df.with_columns(pl.col(col_name).cast(pl.Utf8))

    return df


def _parse_traffic(data: dict[str, Any]) -> pl.DataFrame:
    """Parse traffic statistics into a DataFrame."""
    records = data.get("data", data.get("statistics", []))
    if not records:
        if isinstance(data, dict):
            records = [data]
        else:
            return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
        pl.lit("traffic").alias("stat_type"),
    )
    return df


def _parse_cargo(data: dict[str, Any]) -> pl.DataFrame:
    """Parse cargo volume data into a DataFrame."""
    records = data.get("data", data.get("volumes", []))
    if not records:
        if isinstance(data, dict):
            records = [data]
        else:
            return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
        pl.lit("cargo").alias("stat_type"),
    )
    return df


def collect_data(tracker: SourceTracker | None = None) -> int:
    """Main entry point: fetch Barcelona port data and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        logger.info("Starting Port of Barcelona collection")
        all_frames: list[pl.DataFrame] = []

        calls = fetch_all_vessel_calls(max_pages=5)
        calls_df = _parse_vessel_calls(calls)
        all_frames.append(calls_df)
        logger.info("Vessel calls: %d rows", calls_df.height)

        time.sleep(REQUEST_DELAY_SECONDS)

        try:
            traffic = fetch_traffic_stats()
            traffic_df = _parse_traffic(traffic)
            all_frames.append(traffic_df)
            logger.info("Traffic stats: %d rows", traffic_df.height)
        except Exception:
            logger.exception("Traffic stats fetch failed")

        time.sleep(REQUEST_DELAY_SECONDS)

        try:
            cargo = fetch_cargo_volumes()
            cargo_df = _parse_cargo(cargo)
            all_frames.append(cargo_df)
            logger.info("Cargo volumes: %d rows", cargo_df.height)
        except Exception:
            logger.exception("Cargo volumes fetch failed")

        combined = pl.concat(all_frames)
        tc.rows_fetched = combined.height

        if combined.height == 0:
            logger.warning("No data collected from Barcelona port")
            return 0

        logger.info("Writing %d Barcelona port records", combined.height)
        count = write_raw(SOURCE, combined, table_name="port_calls")
        tc.rows_written = count
        return count
