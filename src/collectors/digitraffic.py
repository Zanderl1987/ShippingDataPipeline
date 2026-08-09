"""Collect Baltic Sea AIS positions, vessel metadata, port calls, and ports from Digitraffic.

Digitraffic (Fintraffic / Finnish Transport Infrastructure Agency) publishes
open marine traffic data covering Finnish and Baltic Sea waters. No auth, no
rate limit documented (reasonable use). Data is refreshed about once a minute.

Endpoints (swagger: https://meri.digitraffic.fi/swagger/):
- /api/ais/v1/locations        GeoJSON FeatureCollection of live positions
- /api/ais/v1/vessels          vessel metadata (name, IMO, destination, ETA)
- /api/port-call/v1/port-calls planned/current port calls in Finnish ports
- /api/port-call/v1/ports      global port/location reference (SSN locations)
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://meri.digitraffic.fi"
LOCATIONS_URL = f"{BASE_URL}/api/ais/v1/locations"
VESSELS_URL = f"{BASE_URL}/api/ais/v1/vessels"
PORT_CALLS_URL = f"{BASE_URL}/api/port-call/v1/port-calls"
PORTS_URL = f"{BASE_URL}/api/port-call/v1/ports"

SOURCE = "digitraffic"

_NAV_STATUS = {
    0: "under_way_using_engine",
    1: "at_anchor",
    2: "not_under_command",
    3: "restricted_maneuverability",
    4: "constrained_by_her_draught",
    5: "moored",
    6: "aground",
    7: "engaged_in_fishing",
    8: "under_way_sailing",
    9: "reserved",
}


def fetch_locations() -> dict[str, Any]:
    """Fetch the live Baltic Sea AIS position feed (GeoJSON)."""
    resp = get_with_retry(LOCATIONS_URL, timeout=30, source=SOURCE)
    data: dict[str, Any] = resp.json()
    return data


def fetch_vessels() -> list[dict[str, Any]]:
    """Fetch vessel metadata for the Baltic Sea feed."""
    resp = get_with_retry(VESSELS_URL, timeout=30, source=SOURCE)
    data = resp.json()
    return data if isinstance(data, list) else []


def fetch_port_calls() -> list[dict[str, Any]]:
    """Fetch planned/current port calls at Finnish ports."""
    resp = get_with_retry(PORT_CALLS_URL, timeout=30, source=SOURCE)
    data = resp.json()
    calls = data.get("portCalls", []) if isinstance(data, dict) else []
    return calls if isinstance(calls, list) else []


def fetch_ports() -> list[dict[str, Any]]:
    """Fetch the global port/location reference (SSN locations).

    The payload is a GeoJSON document whose ``ssnLocations`` feature list
    covers ports and coastal locations worldwide with UN/LOCODE identifiers.
    """
    resp = get_with_retry(PORTS_URL, timeout=60, source=SOURCE)
    data = resp.json()
    if not isinstance(data, dict):
        return []
    locations = data.get("ssnLocations")
    features = locations.get("features", []) if isinstance(locations, dict) else []
    return features if isinstance(features, list) else []


def _epoch_ms_to_ts(ms: Any) -> str | None:
    """Convert an epoch-milliseconds value to an ISO timestamp, or None."""
    if ms is None:
        return None
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=UTC).isoformat()
    except (ValueError, TypeError, OSError):
        return None


def _parse_locations(
    data: dict[str, Any], vessels: list[dict[str, Any]]
) -> pl.DataFrame:
    """Parse the GeoJSON feed into the ais_positions schema.

    Joins the vessel metadata feed (keyed by MMSI) so position rows carry
    vessel_name / imo / destination / draught / eta like the other AIS sources.
    """
    features = data.get("features", [])
    if not features:
        return pl.DataFrame()

    by_mmsi = {str(v.get("mmsi")): v for v in vessels}

    records: list[dict[str, Any]] = []
    for f in features:
        props = f.get("properties", {})
        geom = f.get("geometry") or {}
        coords = geom.get("coordinates") or [None, None]

        mmsi = props.get("mmsi")
        meta = by_mmsi.get(str(mmsi), {})
        ts = _epoch_ms_to_ts(props.get("timestampExternal"))

        # AIS draught is reported in decimeters; the schema stores meters.
        draught_dm = meta.get("draught")
        draught_m = None
        if isinstance(draught_dm, (int, float)) and draught_dm > 0:
            draught_m = round(draught_dm / 10, 2)

        records.append({
            "mmsi": int(mmsi) if mmsi is not None else None,
            "imo": meta.get("imo"),
            "vessel_name": meta.get("name", ""),
            "vessel_type": str(meta.get("shipType", "")),
            "latitude": coords[1] if len(coords) > 1 else None,
            "longitude": coords[0] if coords else None,
            "sog": props.get("sog"),
            "cog": props.get("cog"),
            "heading": props.get("heading"),
            "nav_status": _NAV_STATUS.get(props.get("navStat"), str(props.get("navStat"))),
            "draught": draught_m,
            "destination": meta.get("destination"),
            "timestamp": ts,
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )
    return df


def _parse_vessels(data: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse the vessel metadata feed into the vessels schema."""
    if not data:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for v in data:
        records.append({
            "imo": v.get("imo"),
            "mmsi": v.get("mmsi"),
            "vessel_name": v.get("name", ""),
            "vessel_type": str(v.get("shipType", "")),
            "callsign": v.get("callSign"),
        })

    df = pl.DataFrame(records)
    df = df.with_columns(pl.lit(SOURCE).alias("source"))
    return df


def _parse_port_calls(data: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse the port call feed into the port_calls schema."""
    if not data:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for c in data:
        records.append({
            "imo": c.get("imoLloyds"),
            "mmsi": c.get("mmsi"),
            "vessel_name": c.get("vesselName", ""),
            "port_unlocode": c.get("portToVisit", ""),
            "country": c.get("nationality"),
            "event_type": "port_call",
            "event_timestamp": c.get("portCallTimestamp"),
            "previous_port": c.get("prevPort"),
            "next_port": c.get("nextPort"),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    df = df.with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )
    return df


def _parse_ports(data: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse the SSN location reference into the ports schema.

    Each feature is a point location identified by a UN/LOCODE (locode);
    the first two characters are the ISO 3166-1 alpha-2 country code.
    Locations without coordinates are dropped since a port reference row
    without a position is of little use for joins.
    """
    if not data:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for f in data:
        props = f.get("properties") or {}
        locode = props.get("locode")
        name = props.get("locationName") or ""
        country = props.get("country") or ""
        geom = f.get("geometry")
        if not isinstance(locode, str) or not locode:
            continue
        coords = geom.get("coordinates") if isinstance(geom, dict) else None
        if not isinstance(coords, list) or len(coords) < 2:
            continue
        records.append({
            "unlocode": locode,
            "port_name": name,
            "country": country,
            "country_code": locode[:2].upper(),
            "latitude": coords[1],
            "longitude": coords[0],
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    df = df.with_columns(pl.lit(SOURCE).alias("source"))
    return df


def collect_locations(tracker: SourceTracker | None = None) -> int:
    """Collect live Baltic Sea AIS positions and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = fetch_locations()
        vessels = fetch_vessels()
        df = _parse_locations(raw, vessels)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Digitraffic location data returned")
            return 0

        logger.info("Writing %d Digitraffic position records", df.height)
        count = write_raw(SOURCE, df)
        tc.rows_written = count
        return count


def collect_vessels(tracker: SourceTracker | None = None) -> int:
    """Collect Digitraffic vessel metadata and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, "digitraffic_vessels") as tc:
        raw = fetch_vessels()
        df = _parse_vessels(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Digitraffic vessel data returned")
            return 0

        logger.info("Writing %d Digitraffic vessel records", df.height)
        count = write_raw(SOURCE, df, table_name="vessels")
        tc.rows_written = count
        return count


def collect_port_calls(tracker: SourceTracker | None = None) -> int:
    """Collect Digitraffic port calls and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, "digitraffic_port_calls") as tc:
        raw = fetch_port_calls()
        df = _parse_port_calls(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Digitraffic port call data returned")
            return 0

        logger.info("Writing %d Digitraffic port call records", df.height)
        count = write_raw(SOURCE, df, table_name="port_calls")
        tc.rows_written = count
        return count


def collect_ports(tracker: SourceTracker | None = None) -> int:
    """Collect the Digitraffic port/location reference and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, "digitraffic_ports") as tc:
        raw = fetch_ports()
        df = _parse_ports(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Digitraffic port reference data returned")
            return 0

        logger.info("Writing %d Digitraffic port reference records", df.height)
        count = write_raw(SOURCE, df, table_name="ports")
        tc.rows_written = count
        return count
