from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://axiomoverwatch.io/api/v1"

SOURCE = "axiomancer"

VESSEL_TYPE_MAP: dict[str, str] = {
    "bulk_carrier": "Bulk Carrier",
    "container": "Container Ship",
    "tanker": "Tanker",
    "general_cargo": "General Cargo",
    "vehicle_carrier": "Vehicle Carrier",
    "passenger": "Passenger",
    "fishing": "Fishing",
    "tug": "Tug",
    "sailing": "Sailing",
    "pleasure": "Pleasure Craft",
    "naval": "Naval",
    "research": "Research",
    "cruise": "Cruise",
    "lng": "LNG Carrier",
    "lpg": "LPG Carrier",
    "chemical": "Chemical Tanker",
    "roro": "RoRo",
    "dredger": "Dredger",
    "offshore": "Offshore",
}


def fetch_positions_latest(
    *,
    vessel_type: str | None = None,
    west: float | None = None,
    south: float | None = None,
    east: float | None = None,
    north: float | None = None,
) -> dict[str, Any]:
    """Fetch global latest positions snapshot from Axiomancer Overwatch.

    Returns raw GeoJSON FeatureCollection dict.
    Endpoint is public, no auth required, CDN-cached 5 min.
    """
    params: dict[str, Any] = {}
    if vessel_type:
        params["type"] = vessel_type
    if all(v is not None for v in [west, south, east, north]):
        params["west"] = west
        params["south"] = south
        params["east"] = east
        params["north"] = north

    url = f"{BASE_URL}/positions/latest"
    logger.info("Fetching positions from %s (params=%s)", url, params)

    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def fetch_port_positions(port: str) -> list[dict[str, Any]]:
    """Fetch latest positions for all vessels at a given port."""
    url = f"{BASE_URL}/positions"
    logger.info("Fetching positions for port=%s", port)

    resp = requests.get(url, params={"port": port}, timeout=30)
    resp.raise_for_status()
    result: list[dict[str, Any]] = resp.json()
    return result


def fetch_vessels(
    *,
    port: str | None = None,
    vessel_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Search vessels by port and/or type."""
    url = f"{BASE_URL}/vessels"
    params: dict[str, Any] = {"limit": min(limit, 200)}
    if port:
        params["port"] = port
    if vessel_type:
        params["type"] = vessel_type

    logger.info("Fetching vessels (params=%s)", params)
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    data: dict[str, Any] = resp.json()
    result: list[dict[str, Any]] = data.get("vessels", [])
    return result


def _parse_global_snapshot(data: dict[str, Any]) -> pl.DataFrame:
    """Parse GeoJSON FeatureCollection into a Polars DataFrame."""
    features = data.get("features", [])
    if not features:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [None, None])

        imo_str = props.get("imo", "")
        try:
            imo = int(imo_str) if imo_str else None
        except (ValueError, TypeError):
            imo = None

        records.append(
            {
                "imo": imo,
                "vessel_name": props.get("name"),
                "vessel_type": props.get("vessel_type"),
                "latitude": coords[1] if len(coords) > 1 else None,
                "longitude": coords[0] if len(coords) > 0 else None,
                "sog": props.get("speed"),
                "cog": props.get("course"),
                "heading": None,
                "nav_status": props.get("nav_status"),
                "draught": props.get("draft"),
                "destination": props.get("destination"),
                "eta": None,
                "flag": props.get("flag"),
                "timestamp": props.get("timestamp"),
            }
        )

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    if "timestamp" in df.columns:
        df = df.with_columns(
            pl.col("timestamp")
            .str.to_datetime("%Y-%m-%dT%H:%M:%SZ", strict=False)
            .alias("timestamp")
        )

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def _parse_port_positions(data: list[dict[str, Any]]) -> pl.DataFrame:
    """Parse port positions list into a Polars DataFrame."""
    if not data:
        return pl.DataFrame()

    df = pl.DataFrame(data)

    col_map = {
        "imo_number": "imo",
        "name": "vessel_name",
        "vessel_type": "vessel_type",
        "latitude": "latitude",
        "longitude": "longitude",
        "speed": "sog",
        "course": "cog",
        "draft": "draught",
        "nav_status": "nav_status",
        "destination": "destination",
    }

    rename_map = {k: v for k, v in col_map.items() if k in df.columns and k != v}
    if rename_map:
        df = df.rename(rename_map)

    if "imo" in df.columns:
        df = df.with_columns(
            pl.col("imo").cast(pl.Int64, strict=False)
        )

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_global_snapshot(
    *,
    vessel_type: str | None = None,
    bbox: tuple[float, float, float, float] | None = None,
) -> int:
    """Fetch global positions snapshot and write to storage.

    Args:
        vessel_type: Optional filter (e.g. "container", "tanker").
        bbox: Optional (west, south, east, north) bounding box.

    Returns:
        Number of rows written.
    """
    kwargs: dict[str, Any] = {}
    if vessel_type:
        kwargs["vessel_type"] = vessel_type
    if bbox:
        kwargs["west"], kwargs["south"], kwargs["east"], kwargs["north"] = bbox

    raw = fetch_positions_latest(**kwargs)
    df = _parse_global_snapshot(raw)
    if df.height == 0:
        logger.warning("No positions returned")
        return 0

    logger.info("Writing %d positions to storage", df.height)
    count = write_raw(SOURCE, df)
    return count


def collect_port(port: str) -> int:
    """Fetch positions at a specific port and write to storage.

    Returns number of rows written.
    """
    raw = fetch_port_positions(port)
    df = _parse_port_positions(raw)
    if df.height == 0:
        logger.warning("No positions returned for port %s", port)
        return 0

    logger.info("Writing %d positions for port %s", df.height, port)
    count = write_raw(SOURCE, df)
    return count
