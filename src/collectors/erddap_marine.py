"""Collect sea surface temperature (SST) from ERDDAP MUR SST dataset.

Source: NOAA CoastWatch ERDDAP
Dataset: jplMURSST41 (MUR SST, 0.01° resolution, ~1 day latency)
Variable: analysed_sst (double, °C)
No authentication required.

Each run asks for the last LOOKBACK_DAYS days at each location and stamps rows
with the observation time, so the table's dedup keys collapse re-fetched days
and a day missed by one run (CoastWatch intermittently returns 403 to GitHub
Actions runners) is filled in by the next. MUR masks land and lakes as NaN, so
every location must be an open-water grid cell; each was checked live.
"""
from __future__ import annotations

import csv
import io
import logging
import math
from datetime import UTC, date, datetime, timedelta
from typing import Any

import polars as pl
import requests

from src.collectors.http_utils import get_with_retry
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://coastwatch.pfeg.noaa.gov/erddap/griddap"
DATASET = "jplMURSST41"
VARIABLE = "analysed_sst"
SOURCE = "erddap"
#: Registered name in collect_all; the tracker row must use it.
TRACKER_NAME = "erddap_marine"

LOOKBACK_DAYS = 7

CHOKEPOINTS: dict[str, tuple[float, float]] = {
    "Hormuz": (26.25, 56.0),
    "Malacca/Singapore": (2.0, 102.25),
    # The canal itself is narrower than a grid cell; use the Port Said entrance.
    "Suez Canal (Port Said)": (31.35, 32.35),
    # Gatun Lake: a lake, but not masked.
    "Panama": (9.15, -79.8),
}

PORTS: dict[str, tuple[float, float]] = {
    "Los Angeles": (33.70, -118.22),
    "Shanghai": (31.3, 122.0),
    "Singapore": (1.26, 103.83),
    "Rotterdam": (51.9, 4.0),
    "Busan": (35.05, 129.05),
    "Suez (Gulf of Suez)": (29.9, 32.55),
}

ALL_LOCATIONS: dict[str, tuple[float, float]] = {**CHOKEPOINTS, **PORTS}


def _build_query_url(lat: float, lon: float, start: date) -> str:
    """Build an ERDDAP griddap URL for start..latest at the nearest grid cell."""
    return (
        f"{BASE_URL}/{DATASET}.csv?"
        f"{VARIABLE}"
        f"[({start.isoformat()}T00:00:00Z):1:(last)]"
        f"[({lat})]"
        f"[({lon})]"
    )


def _parse_sst_rows(csv_text: str) -> list[tuple[datetime, float]]:
    """Parse (observation time in naive UTC, SST) pairs from an ERDDAP CSV response.

    Skips the units row, blanks, and NaN (masked land).
    """
    if not csv_text or not csv_text.strip():
        return []

    reader = csv.reader(io.StringIO(csv_text))
    try:
        header = next(reader)
    except StopIteration:
        return []

    if "analysed_sst" not in header or "time" not in header:
        return []

    sst_idx = header.index("analysed_sst")
    time_idx = header.index("time")
    rows: list[tuple[datetime, float]] = []
    for row in reader:
        if len(row) <= max(sst_idx, time_idx):
            continue
        try:
            # Naive UTC: marine_weather.timestamp is a plain TIMESTAMP, and
            # DuckDB would shift an aware value into the host's local zone.
            ts = datetime.fromisoformat(row[time_idx].strip().replace("Z", "+00:00"))
            ts = ts.astimezone(UTC).replace(tzinfo=None)
            sst = float(row[sst_idx].strip())
        except ValueError:
            continue
        if math.isnan(sst):
            continue
        rows.append((ts, sst))
    return rows


def _fetch_sst(lat: float, lon: float, start: date) -> list[tuple[datetime, float]] | None:
    """Fetch SST rows for one location; None if the request failed."""
    url = _build_query_url(lat, lon, start)
    try:
        resp = get_with_retry(url, timeout=30, source=SOURCE)
        return _parse_sst_rows(resp.text)
    except requests.HTTPError as e:
        body = e.response.text[:300] if e.response is not None else ""
        status = e.response.status_code if e.response is not None else "?"
        logger.warning(
            "ERDDAP HTTP %s for (%s, %s): %s", status, lat, lon, body.strip() or "<empty body>"
        )
        return None
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

    with TimedCollector(tracker, TRACKER_NAME) as tc:
        records: list[dict[str, Any]] = []
        today = date.today()
        start = today - timedelta(days=LOOKBACK_DAYS)
        failed = 0

        for name, (lat, lon) in ALL_LOCATIONS.items():
            rows = _fetch_sst(lat, lon, start)
            if rows is None:
                failed += 1
                continue
            if not rows:
                logger.warning("ERDDAP returned no SST values for %s (%s, %s)", name, lat, lon)
            for ts, sst in rows:
                records.append({
                    "timestamp": ts,
                    "latitude": lat,
                    "longitude": lon,
                    "sea_surface_temperature": sst,
                    "source": SOURCE,
                    "partition_date": today,
                })

        if failed == len(ALL_LOCATIONS):
            logger.error("ERDDAP: all %d location requests failed", failed)

        tc.rows_fetched = len(records)
        if not records:
            logger.warning("No ERDDAP SST data returned for any location")
            return 0

        df = pl.DataFrame(records)
        logger.info("Writing %d ERDDAP SST records", df.height)
        count = write_raw(SOURCE, df, table_name="marine_weather")
        tc.rows_written = count
        return count
