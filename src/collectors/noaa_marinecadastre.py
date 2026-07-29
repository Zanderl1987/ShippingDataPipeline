"""Collect historical AIS data from NOAA MarineCadastre."""
from __future__ import annotations

import logging
from datetime import date
from pathlib import Path

import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://coast.noaa.gov/data/marinecadastre/ais"

SOURCE = "noaa_marinecadastre"

# Known bulk download URLs for recent years
BULK_URLS: dict[int, str] = {
    2025: f"{BASE_URL}/2025/container/ais_vessel_2025_01.parquet",
    2024: f"{BASE_URL}/2024/container/ais_vessel_2024_01.parquet",
    2023: f"{BASE_URL}/2023/container/ais_vessel_2023_01.parquet",
}


def get_bulk_download_urls(year: int) -> list[str]:
    """Get available bulk download URLs for a year.

    Args:
        year: Year to get URLs for.

    Returns:
        List of download URLs.
    """
    urls: list[str] = []

    if year in BULK_URLS:
        urls.append(BULK_URLS[year])
    else:
        for month in range(1, 13):
            url = f"{BASE_URL}/{year}/container/ais_vessel_{year}_{month:02d}.parquet"
            urls.append(url)

    return urls


def download_file(url: str, dest: Path) -> bool:
    """Download a file from URL.

    Args:
        url: Source URL.
        dest: Destination path.

    Returns:
        True if successful.
    """
    logger.info("Downloading %s", url)

    try:
        resp = requests.get(url, timeout=300, stream=True)
        resp.raise_for_status()

        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        # Integrity check: file must be > 1KB (a truncated download is useless)
        if dest.stat().st_size < 1024:
            logger.warning(
                "Downloaded file %s is suspiciously small (%d bytes), removing",
                dest, dest.stat().st_size,
            )
            dest.unlink(missing_ok=True)
            return False

        logger.info("Downloaded to %s", dest)
        return True

    except requests.RequestException as e:
        logger.warning("Failed to download %s: %s", url, e)
        return False


def _parse_ais_parquet(file_path: Path) -> pl.DataFrame:
    """Parse an AIS parquet file into a DataFrame.

    Expected columns from NOAA MarineCadastre:
    - MMSI, IMO, VesselName, CallSign, VesselType, Status,
      Length, Width, Draft, Cargo, SOG, COG, Heading,
      DateTime, BaseDateTime, LAT, LON, TransceiverClass
    """
    try:
        df = pl.read_parquet(file_path)
    except Exception as e:
        logger.warning("Failed to read parquet %s: %s", file_path, e)
        return pl.DataFrame()

    if df.height == 0:
        return pl.DataFrame()

    col_map = {
        "MMSI": "mmsi",
        "IMO": "imo",
        "VesselName": "vessel_name",
        "CallSign": "callsign",
        "VesselType": "vessel_type",
        "Status": "nav_status",
        "Length": "length_m",
        "Width": "beam_m",
        "Draft": "draught",
        "SOG": "sog",
        "COG": "cog",
        "Heading": "heading",
        "BaseDateTime": "timestamp",
        "LAT": "latitude",
        "LON": "longitude",
    }

    rename_map = {k: v for k, v in col_map.items() if k in df.columns}
    df = df.rename(rename_map)

    if "timestamp" in df.columns:
        df = df.with_columns(
            pl.col("timestamp").str.to_datetime(strict=False).alias("timestamp")
        )

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_bulk_download(
    year: int,
    month: int | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Download and ingest AIS data from NOAA bulk files.

    Args:
        year: Year to download.
        month: Optional specific month (1-12). If None, downloads all available.
        tracker: Optional SourceTracker.

    Returns:
        Total rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    total_written = 0
    data_dir = settings.data_dir / "noaa_marinecadastre"

    if month:
        urls = [f"{BASE_URL}/{year}/container/ais_vessel_{year}_{month:02d}.parquet"]
    else:
        urls = get_bulk_download_urls(year)

    for url in urls:
        filename = url.split("/")[-1]
        dest = data_dir / filename

        with TimedCollector(tracker, SOURCE) as tc:
            try:
                success = download_file(url, dest)
                if not success:
                    tc.rows_fetched = 0
                    tc.rows_written = 0
                    continue

                df = _parse_ais_parquet(dest)
                tc.rows_fetched = df.height

                if df.height == 0:
                    logger.warning("No data in %s", filename)
                    continue

                count = write_raw(SOURCE, df)
                tc.rows_written = count
                total_written += count
                logger.info("Wrote %d rows from %s", count, filename)

            except Exception as e:
                logger.error("Error processing %s: %s", filename, e)
                tc.rows_fetched = 0
                tc.rows_written = 0

    return total_written
