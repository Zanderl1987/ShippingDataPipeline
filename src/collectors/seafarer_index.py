"""Collect vessel and port registry from Seafarer Index."""
from __future__ import annotations

import logging
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://seafarerindex.com/api/data"

SOURCE = "seafarer_index"


def fetch_ships() -> list[dict[str, Any]]:
    """Fetch full ship registry from Seafarer Index.

    Returns list of ship dicts. ~537KB JSON, regenerated daily.
    CC BY 4.0.
    """
    url = f"{BASE_URL}/ships"
    logger.info("Fetching ships from %s", url)
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    result: list[dict[str, Any]] = resp.json()
    return result


def fetch_ports() -> list[dict[str, Any]]:
    """Fetch full port registry from Seafarer Index.

    Returns list of port dicts. ~43.5MB JSON, regenerated daily.
    CC BY 4.0.
    """
    url = f"{BASE_URL}/ports"
    logger.info("Fetching ports from %s (expect ~43MB)", url)
    resp = requests.get(url, timeout=120, stream=True)
    resp.raise_for_status()

    content_length = resp.headers.get("Content-Length")
    if content_length and int(content_length) > 100_000_000:
        logger.warning("Port registry is %s bytes (>100MB), may cause OOM", content_length)

    result: list[dict[str, Any]] = resp.json()
    resp.close()
    return result


def _parse_ships(data: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse ship registry into a Polars DataFrame.

    Live schema nests loa_m/beam_m under a "dimensions" struct and uses
    flag_iso3/dwt/owner_slug/manager_slug rather than the flatter names
    the vessels table expects.
    """
    if not data:
        return pl.DataFrame()

    # infer_schema_length=None: the live feed has ~1000 rows with sparse/
    # optional fields (e.g. wikidata_qid), and polars' default inference
    # sample can miss a column's true type, raising a ComputeError later.
    df = pl.DataFrame(data, infer_schema_length=None)

    if "dimensions" in df.columns:
        df = df.with_columns(
            pl.col("dimensions").struct.field("loa_m").alias("length_m"),
            pl.col("dimensions").struct.field("beam_m").alias("beam_m"),
        ).drop("dimensions")

    col_map = {
        "imo": "imo",
        "mmsi": "mmsi",
        "name": "vessel_name",
        "type": "vessel_type",
        "flag_iso3": "flag",
        "callsign": "callsign",
        "gross_tonnage": "gross_tonnage",
        "dwt": "deadweight_tonnage",
        "year_built": "year_built",
        "owner_slug": "owner_name",
        "manager_slug": "manager_name",
    }

    rename_map = {k: v for k, v in col_map.items() if k in df.columns and k != v}
    if rename_map:
        df = df.rename(rename_map)

    if "imo" in df.columns:
        df = df.with_columns(pl.col("imo").cast(pl.Int64, strict=False))

    if "mmsi" in df.columns:
        df = df.with_columns(pl.col("mmsi").cast(pl.Int64, strict=False))

    if "year_built" in df.columns:
        df = df.with_columns(pl.col("year_built").cast(pl.Int32, strict=False))

    df = df.with_columns(pl.lit(SOURCE).alias("source"))

    return df


def _parse_ports(data: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse port registry into a Polars DataFrame."""
    if not data:
        return pl.DataFrame()

    df = pl.DataFrame(data)

    col_map = {
        "unlocode": "unlocode",
        "name": "port_name",
        "country": "country",
        "country_code": "country_code",
        "latitude": "latitude",
        "longitude": "longitude",
        "timezone": "timezone",
        "region": "region",
    }

    rename_map = {k: v for k, v in col_map.items() if k in df.columns and k != v}
    if rename_map:
        df = df.rename(rename_map)

    df = df.with_columns(pl.lit(SOURCE).alias("source"))

    return df


def collect_ships(tracker: SourceTracker | None = None) -> int:
    """Fetch ship registry and write to storage.

    Args:
        tracker: Optional SourceTracker for recording collection events.

    Returns number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = fetch_ships()
        df = _parse_ships(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No ships returned")
            return 0

        logger.info("Writing %d ships to storage", df.height)
        count = write_raw(SOURCE, df, table_name="vessels")
        tc.rows_written = count
        return count


def collect_ports(tracker: SourceTracker | None = None) -> int:
    """Fetch port registry and write to storage.

    Args:
        tracker: Optional SourceTracker for recording collection events.

    Returns number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = fetch_ports()
        df = _parse_ports(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No ports returned")
            return 0

        logger.info("Writing %d ports to storage", df.height)
        count = write_raw(SOURCE, df, table_name="ports")
        tc.rows_written = count
        return count
