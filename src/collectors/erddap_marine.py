"""Collect sea surface temperature (SST) from ERDDAP MUR SST dataset.

Source: NOAA CoastWatch ERDDAP
Dataset: jplMURSST41 (MUR SST, 0.01° resolution, ~1 day latency)
Variable: analysed_sst (double, °C)
No authentication required.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"
DATASET = "jplMURSST41"
VARIABLE = "analysed_sst"
SOURCE = "erddap"

BBOX_DELTA = 0.05

CHOKEPOINTS: dict[str, tuple[float, float]] = {
    "Hormuz": (26.25, 56.0),
    "Malacca/Singapore": (2.0, 102.25),
    "Suez": (30.65, 32.45),
    "Panama": (9.15, -79.8),
}

PORTS: dict[str, tuple[float, float]] = {
    "Los Angeles": (33.75, -118.27),
    "Shanghai": (31.2, 121.5),
    "Singapore": (1.26, 103.83),
    "Rotterdam": (51.9, 4.0),
    "Busan": (35.1, 129.0),
    "Suez": (29.9, 32.55),
}

ALL_LOCATIONS: dict[str, tuple[float, float]] = {**CHOKEPOINTS, **PORTS}


def _build_query_url(lat: float, lon: float) -> str:
    """Build ERDDAP griddap query URL for the nearest grid point."""
    lat_min = lat - BBOX_DELTA
    lat_max = lat + BBOX_DELTA
    lon_min = lon - BBOX_DELTA
    lon_max = lon + BBOX_DELTA
    return (
        f"{BASE_URL}/{DATASET}.csv?"
        f"{VARIABLE}"
        f"[last]"
        f"[({lat_min}):({lat_max})]"
        f"[({lon_min}):({lon_max})]"
    )


def _parse_sst_value(csv_text: str) -> float | None:
    """Parse the SST value from an ERDDAP CSV response.

    Returns the analysed_sst from the first data row, or None if
    the response is empty or malformed.
    """
    if not csv_text or not csv_text.strip():
        return None

    reader = csv.reader(io.StringIO(csv_text))
    try:
        header = next(reader)
    except StopIteration:
        return None

    if "analysed_sst" not in header:
        return None

    sst_idx = header.index("analysed_sst")
    for row in reader:
        if len(row) > sst_idx:
            val = row[sst_idx].strip()
            if val and val != "":
                try:
                    return float(val)
                except (ValueError, TypeError):
                    continue
    return None


def _fetch_sst(lat: float, lon: float) -> float | None:
    """Fetch SST for a single location from ERDDAP."""
    url = _build_query_url(lat, lon)
    try:
        resp = get_with_retry(url, timeout=30, source=SOURCE)
        return _parse_sst_value(resp.text)
    except Exception:
        logger.warning("ERDDAP request failed for (%s, %s)", lat, lon, exc_info=True)
        return None


def collect_erddap_marine(tracker: SourceTracker | None = None) -> int:
    """Collect MUR SST data for all chokepoints and ports.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        records: list[dict[str, Any]] = []
        today = date.today()

        for name, (lat, lon) in ALL_LOCATIONS.items():
            sst = _fetch_sst(lat, lon)
            if sst is not None:
                records.append({
                    "timestamp": datetime.now(tz=UTC),
                    "latitude": lat,
                    "longitude": lon,
                    "sea_surface_temperature": sst,
                    "source": SOURCE,
                    "partition_date": today,
                })

        tc.rows_fetched = len(records)
        if not records:
            logger.warning("No ERDDAP SST data returned for any location")
            return 0

        df = pl.DataFrame(records)
        logger.info("Writing %d ERDDAP SST records", df.height)
        count = write_raw(SOURCE, df, table_name="marine_weather")
        tc.rows_written = count
        return count
