"""Collect US vessel tracks from NOAA MarineCadastre AIS.

NOAA publishes monthly GeoParquet track files (one row per track, a vessel's
continuous movement) on Azure, listed at INDEX_URL. Data arrive about every
90 days, roughly 145-165 days after collection (MarineCadastre AIS FAQ, May
2026), so this is a historical source, not a live feed. As of 2026-09 the
index runs 2024-01..2025-12. Licence: CC0 1.0.

Each file is ~1.2-1.4 GB, almost all of it the line geometry. DuckDB's httpfs
reads only the other columns (~27 MB per month) straight from the remote
parquet, so the file is never downloaded whole.

An earlier version of this module fetched
``coast.noaa.gov/data/marinecadastre/ais/<year>/container/ais_vessel_<year>_<mm>.parquet``;
that layout never existed (every month 404s, back to 2024-01), and the
collector reported success with 0 rows every week.
"""
from __future__ import annotations

import logging
import re
from datetime import date

import duckdb
import polars as pl

from src.collectors.http_utils import get_with_retry
from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import get_connection, write_raw

logger = logging.getLogger(__name__)

SOURCE = "noaa_marinecadastre"
TABLE = "vessel_tracks_us"

BASE_URL = "https://ocmgeodatastor1.blob.core.windows.net/marinecadastre/aistrack"
INDEX_URL = f"{BASE_URL}/index-aistrack.html"

_TRACK_FILE = re.compile(r"ais-track-(\d{4})-(\d{2})\.parquet")

#: Source columns kept (everything but `geometry`), in file order.
COLUMNS = [
    "mmsi",
    "vessel_name",
    "imo",
    "call_sign",
    "vessel_type",
    "vessel_type_name",
    "status",
    "length",
    "width",
    "draft",
    "cargo",
    "transceiver",
    "duration_minutes",
    "start_time",
    "end_time",
]


def _parse_index(html: str) -> list[date]:
    """Months (first day) that have a track file, oldest first."""
    months = {date(int(y), int(m), 1) for y, m in _TRACK_FILE.findall(html)}
    return sorted(months)


def _fetch_index() -> list[date]:
    resp = get_with_retry(INDEX_URL, timeout=60, source=SOURCE)
    return _parse_index(resp.text)


def _stored_months() -> set[date]:
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT track_month FROM {TABLE} WHERE source = ?", [SOURCE]
        ).fetchall()
    except duckdb.CatalogException:
        return set()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _months_to_fetch(available: list[date], stored: set[date], bulk: bool) -> list[date]:
    """Months to load this run.

    With bulk backfill (CI) every missing month is loaded. Without it (a dev
    machine) only months newer than anything stored, or just the newest month
    when the table is empty, so a local run never pulls two years of tracks.
    """
    missing = [m for m in available if m not in stored]
    if bulk:
        return missing
    if not stored:
        return missing[-1:]
    newest = max(stored)
    return [m for m in missing if m > newest]


def _read_month(month: date) -> pl.DataFrame:
    """Read one month's tracks, all columns but the geometry, from Azure."""
    url = f"{BASE_URL}/ais-track-{month:%Y-%m}.parquet"
    conn = duckdb.connect()
    try:
        conn.execute("INSTALL httpfs; LOAD httpfs;")
        cols = ", ".join(
            # TIMESTAMP_NS -> TIMESTAMP to match the table.
            f"CAST({c} AS TIMESTAMP) AS {c}" if c.endswith("_time") else c
            for c in COLUMNS
        )
        return conn.execute(f"SELECT {cols} FROM read_parquet('{url}')").pl()
    finally:
        conn.close()


def collect_vessel_tracks(
    tracker: SourceTracker | None = None,
    bulk_backfill: bool | None = None,
) -> int:
    """Load every published month not yet in `vessel_tracks_us`.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()
    if bulk_backfill is None:
        bulk_backfill = settings.allow_bulk_backfill

    with TimedCollector(tracker, SOURCE) as tc:
        available = _fetch_index()
        if not available:
            raise RuntimeError(
                f"No ais-track-YYYY-MM.parquet files listed at {INDEX_URL}; "
                "the NOAA layout may have changed"
            )

        months = _months_to_fetch(available, _stored_months(), bulk_backfill)
        if not months:
            logger.info("MarineCadastre: no new track months (latest %s)", available[-1])
            return 0

        fetched = written = 0
        for month in months:
            df = _read_month(month).with_columns(
                pl.lit(month).alias("track_month"),
                pl.lit(SOURCE).alias("source"),
                pl.lit(date.today()).alias("partition_date"),
            )
            fetched += df.height
            written += write_raw(SOURCE, df, table_name=TABLE)
            logger.info("MarineCadastre: wrote %d tracks for %s", df.height, f"{month:%Y-%m}")

        tc.rows_fetched = fetched
        tc.rows_written = written
        return written
