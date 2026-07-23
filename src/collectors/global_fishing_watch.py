from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://gateway.api.globalfishingwatch.org/v3"

SOURCE = "global_fishing_watch"


def _get_auth_headers() -> dict[str, str]:
    """Get authorization headers for GFW API."""
    token = settings.gfw_api_token
    if not token:
        raise ValueError("GFW_API_TOKEN not set in environment")
    return {"Authorization": f"Bearer {token}"}


def search_vessels(
    query: str,
    limit: int = 10,
) -> dict[str, Any]:
    """Search vessels by name, IMO, or MMSI.

    Args:
        query: Search term (vessel name, IMO, or MMSI).
        limit: Max results (default 10).

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/vessels/search"
    params: dict[str, Any] = {
        "query": query,
        "datasets[0]": "public-global-vessel-identity:latest",
        "limit": min(limit, 50),
    }

    logger.info("Searching GFW vessels: query=%s", query)
    resp = requests.get(
        url, params=params, headers=_get_auth_headers(), timeout=30
    )
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_vessel_events(
    vessel_id: str,
    start_date: str,
    end_date: str,
    event_types: list[str] | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    """Get events for a specific vessel.

    Args:
        vessel_id: GFW vessel ID.
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        event_types: Filter by event types (fishing, port_visit, encounter, loitering).
        limit: Max results.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/events"
    params: dict[str, Any] = {
        "vessels[0]": vessel_id,
        "datasets[0]": "public-global-fishing-events:latest",
        "start-date": start_date,
        "end-date": end_date,
        "limit": min(limit, 1000),
        "offset": 0,
    }

    if event_types:
        for i, et in enumerate(event_types):
            params[f"events[{i}]"] = et

    logger.info(
        "Fetching GFW events: vessel=%s, range=%s to %s", vessel_id, start_date, end_date
    )
    resp = requests.get(
        url, params=params, headers=_get_auth_headers(), timeout=60
    )
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_port_visits(
    start_date: str,
    end_date: str,
    limit: int = 100,
) -> dict[str, Any]:
    """Get port visit events across all vessels.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        limit: Max results.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/events"
    params: dict[str, Any] = {
        "datasets[0]": "public-global-fishing-events:latest",
        "events[0]": "port_visit",
        "start-date": start_date,
        "end-date": end_date,
        "limit": min(limit, 1000),
        "offset": 0,
    }

    logger.info("Fetching GFW port visits: %s to %s", start_date, end_date)
    resp = requests.get(
        url, params=params, headers=_get_auth_headers(), timeout=60
    )
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _parse_vessel_search(data: dict[str, Any]) -> pl.DataFrame:
    """Parse vessel search results into a DataFrame."""
    entries = data.get("entries", [])
    if not entries:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for entry in entries:
        registry_info = entry.get("registryInfo", [])
        info = registry_info[0] if registry_info else {}

        records.append({
            "gfw_vessel_id": entry.get("id"),
            "imo": info.get("imo"),
            "mmsi": info.get("mmsi"),
            "vessel_name": info.get("name"),
            "vessel_type": info.get("shipType"),
            "flag": info.get("flag"),
            "length_m": info.get("length"),
            "beam_m": info.get("beam"),
            "gross_tonnage": info.get("grossTonnage"),
            "deadweight_tonnage": info.get("dwt"),
            "year_built": info.get("buildYear"),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    if "imo" in df.columns:
        df = df.with_columns(pl.col("imo").cast(pl.Int64, strict=False))
    if "mmsi" in df.columns:
        df = df.with_columns(pl.col("mmsi").cast(pl.Int64, strict=False))

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def _parse_events(data: dict[str, Any]) -> pl.DataFrame:
    """Parse event results into a DataFrame."""
    entries = data.get("entries", [])
    if not entries:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for entry in entries:
        event_type = entry.get("type")
        start = entry.get("start")
        end = entry.get("end")

        vessel_info = entry.get("vessel", {})
        imo = vessel_info.get("imo")
        mmsi = vessel_info.get("mmsi")
        vessel_name = vessel_info.get("name")

        position = entry.get("position", {})
        lat = position.get("lat")
        lon = position.get("lon")

        port = entry.get("port", {})
        port_unlocode = port.get("unlocode") if port else None
        port_name = port.get("name") if port else None

        records.append({
            "gfw_event_id": entry.get("id"),
            "event_type": event_type,
            "imo": imo,
            "mmsi": mmsi,
            "vessel_name": vessel_name,
            "latitude": lat,
            "longitude": lon,
            "port_unlocode": port_unlocode,
            "port_name": port_name,
            "start_time": start,
            "end_time": end,
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    if "imo" in df.columns:
        df = df.with_columns(pl.col("imo").cast(pl.Int64, strict=False))
    if "mmsi" in df.columns:
        df = df.with_columns(pl.col("mmsi").cast(pl.Int64, strict=False))

    for col in ["start_time", "end_time"]:
        if col in df.columns:
            df = df.with_columns(
                pl.col(col)
                .str.to_datetime("%Y-%m-%dT%H:%M:%SZ", strict=False)
                .alias(col)
            )

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_vessel_search(
    query: str,
    limit: int = 10,
    tracker: SourceTracker | None = None,
) -> int:
    """Search vessels and write results to storage.

    Args:
        query: Search term.
        limit: Max results.
        tracker: Optional SourceTracker.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = search_vessels(query, limit)
        df = _parse_vessel_search(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No vessels found for query: %s", query)
            return 0

        logger.info("Writing %d vessels from GFW search", df.height)
        count = write_raw(SOURCE, df, table_name="vessels")
        tc.rows_written = count
        return count


def collect_events(
    start_date: str,
    end_date: str,
    vessel_id: str | None = None,
    event_types: list[str] | None = None,
    limit: int = 100,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect events and write to storage.

    Args:
        start_date: Start date (YYYY-MM-DD).
        end_date: End date (YYYY-MM-DD).
        vessel_id: Optional specific vessel.
        event_types: Optional event type filter.
        limit: Max results.
        tracker: Optional SourceTracker.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        if vessel_id:
            raw = get_vessel_events(vessel_id, start_date, end_date, event_types, limit)
        else:
            raw = get_port_visits(start_date, end_date, limit)

        df = _parse_events(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No events found")
            return 0

        logger.info("Writing %d events from GFW", df.height)
        count = write_raw(SOURCE, df, table_name="port_calls")
        tc.rows_written = count
        return count
