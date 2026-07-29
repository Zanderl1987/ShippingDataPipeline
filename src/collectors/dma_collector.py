"""Collect Danish Maritime Authority vessel registration data."""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://www.dma.dk"
SOURCE = "dma_vessel_registry"

VESSEL_ENDPOINT = f"{BASE_URL}/Erhverv/Skibsregistret/"
API_SEARCH_URL = f"{BASE_URL}/api/vessels/search"
PAGE_SIZE = 100
REQUEST_DELAY_SECONDS = 2


def fetch_vessel_page(offset: int = 0, limit: int = PAGE_SIZE) -> dict[str, Any]:
    """Fetch a single page of vessel registry data from DMA."""
    params = {
        "offset": str(offset),
        "limit": str(limit),
    }
    resp = get_with_retry(
        API_SEARCH_URL,
        params=params,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def fetch_all_vessels(max_pages: int = 50) -> list[dict[str, Any]]:
    """Iterate through all pages of the DMA vessel registry."""
    all_vessels: list[dict[str, Any]] = []
    for page in range(max_pages):
        offset = page * PAGE_SIZE
        logger.info("Fetching DMA registry page %d (offset=%d)", page, offset)
        try:
            data = fetch_vessel_page(offset=offset)
        except Exception:
            logger.exception("Failed to fetch DMA page at offset %d", offset)
            break

        vessels = data.get("vessels", data.get("results", []))
        if not vessels:
            logger.info("No more vessels at page %d", page)
            break

        all_vessels.extend(vessels)
        time.sleep(REQUEST_DELAY_SECONDS)

    logger.info("Total vessels fetched from DMA: %d", len(all_vessels))
    return all_vessels


def fetch_vessel_detail(imo_number: str) -> dict[str, Any] | None:
    """Fetch detail for a single vessel by IMO number."""
    url = f"{API_SEARCH_URL}/{imo_number}"
    try:
        resp = get_with_retry(url, timeout=60, source=SOURCE)
        return resp.json()
    except Exception:
        logger.warning("Could not fetch detail for IMO %s", imo_number)
        return None


def _parse_vessels(vessels: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse vessel registry records into a DataFrame."""
    if not vessels:
        return pl.DataFrame()

    df = pl.DataFrame(vessels)

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    if "imo_number" in df.columns:
        df = df.with_columns(
            pl.col("imo_number").cast(pl.Utf8).alias("imo_number")
        )

    return df


def _parse_detail(detail: dict[str, Any]) -> pl.DataFrame:
    """Parse a single vessel detail response into a DataFrame."""
    if not detail:
        return pl.DataFrame()

    df = pl.DataFrame([detail])
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )
    return df


def collect_data(tracker: SourceTracker | None = None) -> int:
    """Main entry point: fetch DMA vessel registrations and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        logger.info("Starting DMA vessel registry collection")
        vessels = fetch_all_vessels(max_pages=10)
        df = _parse_vessels(vessels)
        tc.rows_fetched = df.height

        if df.height == 0:
            logger.warning("No vessel data returned from DMA")
            return 0

        logger.info("Writing %d DMA vessel records", df.height)
        count = write_raw(SOURCE, df, table_name="vessel_registry")
        tc.rows_written = count
        return count
