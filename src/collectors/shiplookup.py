from __future__ import annotations

import logging
from typing import Any

import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://shiplookup.com/api"

SOURCE = "shiplookup"


def _get_auth_headers() -> dict[str, str]:
    """Get authorization headers for ShipLookup."""
    key = settings.shiplookup_api_key
    if not key:
        raise ValueError("SHIPLOOKUP_API_KEY not set in environment")
    return {"X-API-Key": key, "Content-Type": "application/json"}


def search_ships(
    *,
    name: str | None = None,
    vessel_name: str | None = None,
    callsign: str | None = None,
    limit: int = 10,
) -> dict[str, Any]:
    """Search for ships by name, vessel name, or callsign.

    Search endpoint does NOT consume credits.

    Args:
        name: Partial name search.
        vessel_name: Partial vessel name search.
        callsign: Partial callsign search.
        limit: Number of results (1-100, default 10).

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/ships/search"
    params: dict[str, Any] = {"limit": min(limit, 100)}

    if name:
        params["name"] = name
    if vessel_name:
        params["vessel_name"] = vessel_name
    if callsign:
        params["callsign"] = callsign

    logger.info("Searching ShipLookup ships (params=%s)", params)
    resp = requests.get(
        url, params=params, headers=_get_auth_headers(), timeout=30
    )
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_ship_by_imo(imo: int) -> dict[str, Any]:
    """Get detailed ship information by IMO number.

    This endpoint consumes 1 credit per request.

    Args:
        imo: IMO number.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/ships/{imo}"

    logger.info("Fetching ShipLookup ship IMO %d", imo)
    resp = requests.get(url, headers=_get_auth_headers(), timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _parse_ship_search(data: dict[str, Any]) -> pl.DataFrame:
    """Parse ship search results into a DataFrame."""
    ships = data.get("data", [])
    if not ships:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for ship in ships:
        imo = ship.get("imo")
        mmsi = ship.get("mmsi")

        records.append({
            "imo": int(imo) if imo else None,
            "mmsi": int(mmsi) if mmsi else None,
            "vessel_name": ship.get("vesselName") or ship.get("name"),
            "vessel_type": ship.get("shipType"),
            "flag": ship.get("flag"),
            "callsign": ship.get("callsign"),
            "length_m": ship.get("length"),
            "beam_m": ship.get("beam"),
            "gross_tonnage": ship.get("grossTonnage"),
            "year_built": ship.get("yearBuilt"),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    for col in ["imo", "mmsi"]:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Int64, strict=False))

    return df


def _parse_ship_detail(data: dict[str, Any]) -> pl.DataFrame:
    """Parse ship detail response into a DataFrame."""
    if not data or not data.get("success"):
        return pl.DataFrame()

    ship = data.get("data", {})
    if not ship:
        return pl.DataFrame()

    imo = ship.get("imo")
    mmsi = ship.get("mmsi")

    records = [{
        "imo": int(imo) if imo else None,
        "mmsi": int(mmsi) if mmsi else None,
        "vessel_name": ship.get("vesselName") or ship.get("name"),
        "vessel_type": ship.get("shipType"),
        "flag": ship.get("flag"),
        "callsign": ship.get("callsign"),
        "length_m": ship.get("length"),
        "beam_m": ship.get("beam"),
        "gross_tonnage": ship.get("grossTonnage"),
        "year_built": ship.get("yearBuilt"),
    }]

    df = pl.DataFrame(records)
    return df


def collect_ship_search(
    *,
    name: str | None = None,
    vessel_name: str | None = None,
    callsign: str | None = None,
    limit: int = 10,
    tracker: SourceTracker | None = None,
) -> int:
    """Search ships and write results to storage.

    Uses the free search endpoint (no credits consumed).

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = search_ships(name=name, vessel_name=vessel_name, callsign=callsign, limit=limit)
        df = _parse_ship_search(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No ships found")
            return 0

        logger.info("Writing %d ships from ShipLookup search", df.height)
        count = write_raw(SOURCE, df, table_name="vessels")
        tc.rows_written = count
        return count


def collect_ship_by_imo(
    imo: int,
    tracker: SourceTracker | None = None,
) -> int:
    """Fetch ship details by IMO and write to storage.

    Consumes 1 credit per request.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_ship_by_imo(imo)
        df = _parse_ship_detail(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No ship data returned for IMO %d", imo)
            return 0

        logger.info("Writing ship IMO %d from ShipLookup", imo)
        count = write_raw(SOURCE, df, table_name="vessels")
        tc.rows_written = count
        return count
