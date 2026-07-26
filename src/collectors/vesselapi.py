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

BASE_URL = "https://api.vesselapi.com/v1"

SOURCE = "vesselapi"


def _get_auth_headers() -> dict[str, str]:
    """Get authorization headers for VesselAPI."""
    key = settings.vesselapi_api_key
    if not key:
        raise ValueError("VESSELAPI_API_KEY not set in environment")
    return {"Authorization": f"Bearer {key}"}


def get_port_events(
    *,
    time_from: str | None = None,
    time_to: str | None = None,
    country: str | None = None,
    unlocode: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    """Get port events within a time range.

    Args:
        time_from: Start time (RFC3339 format).
        time_to: End time (RFC3339 format).
        country: Filter by port country.
        unlocode: Filter by port UN/LOCODE.
        event_type: Filter by event type (arrival, departure, all).
        limit: Results per page (max 50).

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/portevents"
    params: dict[str, Any] = {"pagination.limit": min(limit, 50)}

    if time_from:
        params["time.from"] = time_from
    if time_to:
        params["time.to"] = time_to
    if country:
        params["filter.country"] = country
    if unlocode:
        params["filter.unlocode"] = unlocode
    if event_type:
        params["filter.eventType"] = event_type

    logger.info("Fetching VesselAPI port events (params=%s)", params)
    resp = requests.get(
        url, params=params, headers=_get_auth_headers(), timeout=30
    )
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_port_events_by_port(
    unlocode: str,
    limit: int = 50,
) -> dict[str, Any]:
    """Get all events for a specific port.

    Args:
        unlocode: Port UN/LOCODE (e.g. NLRTM).
        limit: Results per page (max 50).

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/portevents/port/{unlocode}"
    params: dict[str, Any] = {"pagination.limit": min(limit, 50)}

    logger.info("Fetching VesselAPI events for port %s", unlocode)
    resp = requests.get(
        url, params=params, headers=_get_auth_headers(), timeout=30
    )
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_vessel_port_history(
    vessel_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    """Get port call history for a vessel.

    Args:
        vessel_id: Vessel MMSI or IMO.
        limit: Results per page (max 50).

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/portevents/vessel/{vessel_id}"
    params: dict[str, Any] = {"pagination.limit": min(limit, 50)}

    logger.info("Fetching VesselAPI port history for vessel %s", vessel_id)
    resp = requests.get(
        url, params=params, headers=_get_auth_headers(), timeout=30
    )
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_vessel(
    vessel_id: str,
) -> dict[str, Any]:
    """Get vessel details by MMSI or IMO.

    Args:
        vessel_id: Vessel MMSI or IMO.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/vessel/{vessel_id}"

    logger.info("Fetching VesselAPI vessel %s", vessel_id)
    resp = requests.get(url, headers=_get_auth_headers(), timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _parse_port_events(data: dict[str, Any]) -> pl.DataFrame:
    """Parse port events into a DataFrame."""
    events = data.get("portEvents", [])
    if not events:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for event in events:
        vessel = event.get("vessel", {})
        port = event.get("port", {})

        imo = vessel.get("imo")
        mmsi = vessel.get("mmsi")

        records.append({
            "imo": int(imo) if imo else None,
            "mmsi": int(mmsi) if mmsi else None,
            "vessel_name": vessel.get("name"),
            "port_unlocode": port.get("unlocode"),
            "port_name": port.get("name"),
            "country": port.get("country"),
            "event_type": event.get("type"),
            "event_timestamp": event.get("time"),
            "eta": event.get("eta"),
            "etd": event.get("etd"),
            "previous_port": event.get("previousPort"),
            "next_port": event.get("nextPort"),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    for col in ["event_timestamp", "eta", "etd"]:
        if col in df.columns and df[col].dtype == pl.String:
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


def _parse_vessel(data: dict[str, Any]) -> pl.DataFrame:
    """Parse vessel details into a DataFrame."""
    if not data:
        return pl.DataFrame()

    imo = data.get("imo")
    mmsi = data.get("mmsi")

    records = [{
        "imo": int(imo) if imo else None,
        "mmsi": int(mmsi) if mmsi else None,
        "vessel_name": data.get("name"),
        "vessel_type": data.get("type"),
        "flag": data.get("flag"),
        "callsign": data.get("callsign"),
        "length_m": data.get("length"),
        "beam_m": data.get("beam"),
        "gross_tonnage": data.get("grossTonnage"),
        "deadweight_tonnage": data.get("deadweight"),
        "year_built": data.get("yearBuilt"),
        "owner_name": data.get("owner"),
        "manager_name": data.get("manager"),
    }]

    df = pl.DataFrame(records)

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_port_events(
    *,
    time_from: str | None = None,
    time_to: str | None = None,
    country: str | None = None,
    unlocode: str | None = None,
    event_type: str | None = None,
    limit: int = 50,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect port events and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_port_events(
            time_from=time_from,
            time_to=time_to,
            country=country,
            unlocode=unlocode,
            event_type=event_type,
            limit=limit,
        )
        df = _parse_port_events(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No port events returned")
            return 0

        logger.info("Writing %d port events from VesselAPI", df.height)
        count = write_raw(SOURCE, df, table_name="port_calls")
        tc.rows_written = count
        return count


def collect_vessel(
    vessel_id: str,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect vessel details and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_vessel(vessel_id)
        df = _parse_vessel(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No vessel data returned for %s", vessel_id)
            return 0

        logger.info("Writing vessel %s from VesselAPI", vessel_id)
        count = write_raw(SOURCE, df, table_name="vessels")
        tc.rows_written = count
        return count
