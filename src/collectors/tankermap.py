"""Collect tanker positions and port calls from TankerMap."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://tankermap.com/api"
SOURCE = "tankermap"

CRUDE_BBL_PER_TONNE = 7.37
PRODUCT_BBL_PER_TONNE = 7.40


def get_live_vessels(
    vessel_type: str | None = None,
) -> dict[str, Any]:
    """Get live tanker positions from TankerMap.

    Args:
        vessel_type: Optional filter (e.g. "crude_oil", "lng", "oil_products").

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/vessels/live"
    params: dict[str, Any] = {}
    if vessel_type:
        params["type"] = vessel_type

    logger.info("Fetching TankerMap live vessels (type=%s)", vessel_type or "all")
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_port_calls(
    port_slug: str | None = None,
) -> dict[str, Any]:
    """Get port call data from TankerMap.

    Args:
        port_slug: Optional port filter (e.g. "ras-tanura", "saldanha-bay").

    Returns:
        Raw API response dict.
    """
    if port_slug:
        url = f"{BASE_URL}/ports/{port_slug}/calls"
    else:
        url = f"{BASE_URL}/ports"

    logger.info("Fetching TankerMap port calls (port=%s)", port_slug or "all")
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _parse_vessels(data: dict[str, Any]) -> pl.DataFrame:
    """Parse TankerMap vessel positions into ais_positions schema."""
    vessels = data.get("vessels", data.get("data", []))
    if not vessels:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for v in vessels:
        imo_str = str(v.get("imo", ""))
        try:
            imo = int(imo_str) if imo_str else None
        except (ValueError, TypeError):
            imo = None

        mmsi_str = str(v.get("mmsi", ""))
        try:
            mmsi = int(mmsi_str) if mmsi_str else None
        except (ValueError, TypeError):
            mmsi = None

        records.append({
            "mmsi": mmsi,
            "imo": imo,
            "vessel_name": v.get("name", v.get("vessel_name", "")),
            "latitude": v.get("lat", v.get("latitude")),
            "longitude": v.get("lon", v.get("longitude")),
            "sog": v.get("sog", v.get("speed")),
            "cog": v.get("cog", v.get("course")),
            "heading": v.get("heading"),
            "nav_status": v.get("status", v.get("nav_status")),
            "draught": v.get("draught", v.get("draft")),
            "destination": v.get("destination"),
            "eta": v.get("eta"),
            "timestamp": v.get("timestamp", v.get("last_seen")),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def _parse_port_calls(data: dict[str, Any]) -> pl.DataFrame:
    """Parse TankerMap port call data into port_calls schema."""
    calls = data.get("calls", data.get("data", []))
    if not calls:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for c in calls:
        imo_str = str(c.get("imo", ""))
        try:
            imo = int(imo_str) if imo_str else None
        except (ValueError, TypeError):
            imo = None

        mmsi_str = str(c.get("mmsi", ""))
        try:
            mmsi = int(mmsi_str) if mmsi_str else None
        except (ValueError, TypeError):
            mmsi = None

        records.append({
            "imo": imo,
            "mmsi": mmsi,
            "vessel_name": c.get("vessel_name", c.get("name", "")),
            "port_unlocode": c.get("port_unlocode", c.get("port", "")),
            "port_name": c.get("port_name", ""),
            "country": c.get("country", ""),
            "event_type": c.get("event_type", c.get("type", "")),
            "event_timestamp": c.get("timestamp", c.get("event_time")),
            "eta": c.get("eta"),
            "etd": c.get("etd"),
            "previous_port": c.get("previous_port"),
            "next_port": c.get("next_port"),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_vessels(
    vessel_type: str | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect live tanker positions and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_live_vessels(vessel_type=vessel_type)
        df = _parse_vessels(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No TankerMap vessel data returned")
            return 0

        logger.info("Writing %d TankerMap vessel records", df.height)
        count = write_raw(SOURCE, df)
        tc.rows_written = count
        return count


def collect_port_calls(
    port_slug: str | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect port call data and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_port_calls(port_slug=port_slug)
        df = _parse_port_calls(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No TankerMap port call data returned")
            return 0

        logger.info("Writing %d TankerMap port call records", df.height)
        count = write_raw(SOURCE, df, table_name="port_calls")
        tc.rows_written = count
        return count
