"""Collect Singapore MPA vessel traffic and port call data."""
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

BASE_URL = "https://www.mpa.gov.sg"
API_BASE = f"{BASE_URL}/api/v1"
SOURCE = "singapore_mpa"

VESSEL_TRAFFIC_ENDPOINT = f"{API_BASE}/vessel-traffic"
PORT_CALLS_ENDPOINT = f"{API_BASE}/port-calls"
STRAIT_MOVEMENTS_ENDPOINT = f"{API_BASE}/strait-movements"
ANCHORAGE_ENDPOINT = f"{API_BASE}/anchorage-status"

REQUEST_DELAY_SECONDS = 3


def fetch_vessel_traffic(date_str: str | None = None) -> dict[str, Any]:
    """Fetch vessel traffic data for the Singapore Strait."""
    params: dict[str, str] = {}
    if date_str:
        params["date"] = date_str
    resp = get_with_retry(
        VESSEL_TRAFFIC_ENDPOINT,
        params=params,
        timeout=90,
        source=SOURCE,
    )
    return resp.json()


def fetch_port_calls(page: int = 1, limit: int = 100) -> dict[str, Any]:
    """Fetch port call data for Singapore port."""
    params = {
        "page": str(page),
        "limit": str(limit),
    }
    resp = get_with_retry(
        PORT_CALLS_ENDPOINT,
        params=params,
        timeout=90,
        source=SOURCE,
    )
    return resp.json()


def fetch_strait_movements() -> dict[str, Any]:
    """Fetch vessel movements through the Singapore Strait."""
    resp = get_with_retry(
        STRAIT_MOVEMENTS_ENDPOINT,
        timeout=90,
        source=SOURCE,
    )
    return resp.json()


def fetch_anchorage_status() -> dict[str, Any]:
    """Fetch current anchorage occupancy and status."""
    resp = get_with_retry(
        ANCHORAGE_ENDPOINT,
        timeout=90,
        source=SOURCE,
    )
    return resp.json()


def fetch_all_port_calls(max_pages: int = 20) -> list[dict[str, Any]]:
    """Paginate through port call records."""
    all_calls: list[dict[str, Any]] = []
    for page in range(1, max_pages + 1):
        logger.info("Fetching Singapore port calls page %d", page)
        try:
            data = fetch_port_calls(page=page)
        except Exception:
            logger.exception("Failed to fetch Singapore page %d", page)
            break

        records = data.get("data", data.get("results", []))
        if not records:
            logger.info("No more port calls at page %d", page)
            break

        all_calls.extend(records)
        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("Total Singapore port calls fetched: %d", len(all_calls))
    return all_calls


def _parse_vessel_traffic(data: dict[str, Any]) -> pl.DataFrame:
    """Parse vessel traffic records into a DataFrame."""
    records = data.get("data", data.get("vessels", data.get("results", [])))
    if not records:
        if isinstance(data, dict) and "vessel_count" in data:
            records = [data]
        else:
            return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
        pl.lit("vessel_traffic").alias("data_subtype"),
    )
    return df


def _parse_port_calls(records: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse port call records into a DataFrame."""
    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
        pl.lit("port_calls").alias("data_subtype"),
    )

    for col_name in ("imo", "mmsi", "vessel_name", "flag", "terminal"):
        if col_name in df.columns:
            df = df.with_columns(pl.col(col_name).cast(pl.Utf8))

    return df


def _parse_strait_movements(data: dict[str, Any]) -> pl.DataFrame:
    """Parse strait movement records into a DataFrame."""
    records = data.get("data", data.get("movements", data.get("results", [])))
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
        pl.lit("strait_movements").alias("data_subtype"),
    )
    return df


def _parse_anchorage(data: dict[str, Any]) -> pl.DataFrame:
    """Parse anchorage status into a DataFrame."""
    records = data.get("data", data.get("anchorages", data.get("berths", [])))
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
        pl.lit("anchorage").alias("data_subtype"),
    )
    return df


def collect_data(tracker: SourceTracker | None = None) -> int:
    """Main entry point: fetch Singapore MPA data and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        logger.info("Starting Singapore MPA collection")
        all_frames: list[pl.DataFrame] = []

        try:
            traffic_raw = fetch_vessel_traffic()
            traffic_df = _parse_vessel_traffic(traffic_raw)
            all_frames.append(traffic_df)
            logger.info("Vessel traffic: %d rows", traffic_df.height)
        except Exception:
            logger.exception("Vessel traffic fetch failed")

        time.sleep(REQUEST_DELAY_SECONDS)

        calls = fetch_all_port_calls(max_pages=10)
        calls_df = _parse_port_calls(calls)
        all_frames.append(calls_df)
        logger.info("Port calls: %d rows", calls_df.height)

        time.sleep(REQUEST_DELAY_SECONDS)

        try:
            strait_raw = fetch_strait_movements()
            strait_df = _parse_strait_movements(strait_raw)
            all_frames.append(strait_df)
            logger.info("Strait movements: %d rows", strait_df.height)
        except Exception:
            logger.exception("Strait movements fetch failed")

        time.sleep(REQUEST_DELAY_SECONDS)

        try:
            anchorage_raw = fetch_anchorage_status()
            anchorage_df = _parse_anchorage(anchorage_raw)
            all_frames.append(anchorage_df)
            logger.info("Anchorage status: %d rows", anchorage_df.height)
        except Exception:
            logger.exception("Anchorage status fetch failed")

        combined = pl.concat(all_frames)
        tc.rows_fetched = combined.height

        if combined.height == 0:
            logger.warning("No data collected from Singapore MPA")
            return 0

        logger.info("Writing %d Singapore MPA records", combined.height)
        count = write_raw(SOURCE, combined, table_name="port_calls")
        tc.rows_written = count
        return count
