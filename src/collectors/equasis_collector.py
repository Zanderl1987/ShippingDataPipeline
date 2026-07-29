"""Collect vessel safety and inspection data from Equasis."""
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

BASE_URL = "https://www.equasis.org"
API_BASE = f"{BASE_URL}/EquasisWeb"
SOURCE = "equasis"

VESSEL_SEARCH_ENDPOINT = f"{API_BASE}/Public/ShipSearch"
VESSEL_DETAIL_ENDPOINT = f"{API_BASE}/Public/ShipInfo"
INSPECTION_ENDPOINT = f"{API_BASE}/Public/InspectionHistory"

REQUEST_DELAY_SECONDS = 3
BATCH_SIZE = 50


def fetch_vessel_search(vessel_name: str | None = None, imo: str | None = None) -> dict[str, Any]:
    """Search for a vessel on Equasis by name or IMO number."""
    params: dict[str, str] = {}
    if vessel_name:
        params["shipname"] = vessel_name
    if imo:
        params["imo"] = imo
    resp = get_with_retry(
        VESSEL_SEARCH_ENDPOINT,
        params=params,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def fetch_vessel_detail(imo: str) -> dict[str, Any]:
    """Fetch full vessel particulars from Equasis."""
    params = {"imo": imo}
    resp = get_with_retry(
        VESSEL_DETAIL_ENDPOINT,
        params=params,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def fetch_inspection_history(imo: str) -> dict[str, Any]:
    """Fetch inspection and detention history for a vessel."""
    params = {"imo": imo}
    resp = get_with_retry(
        INSPECTION_ENDPOINT,
        params=params,
        timeout=60,
        source=SOURCE,
    )
    return resp.json()


def fetch_vessel_batch(imo_list: list[str]) -> list[dict[str, Any]]:
    """Fetch detail and inspection data for a batch of IMO numbers."""
    results: list[dict[str, Any]] = []
    for imo in imo_list:
        logger.debug("Fetching Equasis data for IMO %s", imo)
        try:
            detail = fetch_vessel_detail(imo)
            record = detail.get("vessel", detail)
            record["imo_number"] = imo
            results.append(record)
        except Exception:
            logger.warning("Failed to fetch Equasis detail for IMO %s", imo)

        try:
            inspection = fetch_inspection_history(imo)
            inspections = inspection.get("inspections", [])
            for insp in inspections:
                insp["imo_number"] = imo
            results.extend(inspections)
        except Exception:
            logger.warning("Failed to fetch Equasis inspections for IMO %s", imo)

        time.sleep(REQUEST_DELAY_SECONDS)

    return results


def _parse_vessel_details(records: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse vessel detail records into a DataFrame."""
    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    for col_name in ("imo_number", "mmsi", "vessel_name", "flag_state", "ship_type"):
        if col_name in df.columns:
            df = df.with_columns(pl.col(col_name).cast(pl.Utf8))

    return df


def _parse_inspections(records: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse inspection records into a DataFrame."""
    inspection_records = [r for r in records if "inspector" in r or "inspection_date" in r or "deficiency" in r]
    if not inspection_records:
        return pl.DataFrame()

    df = pl.DataFrame(inspection_records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )
    return df


def collect_data(
    tracker: SourceTracker | None = None,
    imo_list: list[str] | None = None,
) -> int:
    """Main entry point: fetch Equasis vessel data and write to storage.

    Args:
        tracker: Optional SourceTracker instance.
        imo_list: Optional list of IMO numbers to fetch. If None, fetches
                  a default set or reads from configuration.
    """
    if tracker is None:
        tracker = SourceTracker()

    if imo_list is None:
        imo_list = getattr(settings, "EQUASIS_IMO_LIST", [])

    with TimedCollector(tracker, SOURCE) as tc:
        logger.info("Starting Equasis collection for %d IMO numbers", len(imo_list))

        if not imo_list:
            logger.warning("No IMO numbers configured for Equasis collection")
            tc.rows_fetched = 0
            return 0

        batches = [
            imo_list[i : i + BATCH_SIZE] for i in range(0, len(imo_list), BATCH_SIZE)
        ]

        all_frames: list[pl.DataFrame] = []

        for batch_idx, batch in enumerate(batches):
            logger.info(
                "Processing Equasis batch %d/%d (%d IMOs)",
                batch_idx + 1,
                len(batches),
                len(batch),
            )
            records = fetch_vessel_batch(batch)

            vessel_details = [r for r in records if "vessel_name" in r or "ship_type" in r]
            if vessel_details:
                detail_df = _parse_vessel_details(vessel_details)
                all_frames.append(detail_df)

            inspection_df = _parse_inspections(records)
            if inspection_df.height > 0:
                all_frames.append(inspection_df)

            time.sleep(REQUEST_DELAY_SECONDS)

        if not all_frames:
            logger.warning("No Equasis data collected")
            tc.rows_fetched = 0
            return 0

        combined = pl.concat(all_frames)
        tc.rows_fetched = combined.height

        logger.info("Writing %d Equasis records", combined.height)
        count = write_raw(SOURCE, combined, table_name="vessel_safety")
        tc.rows_written = count
        return count
