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

BASE_URL = "https://live.ais.barentswatch.no/v1"
HISTORIC_URL = "https://historic.ais.barentswatch.no/v1"

SOURCE = "barentswatch"


def _get_auth_headers() -> dict[str, str]:
    """Get authorization headers for BarentsWatch."""
    token = getattr(settings, "barentswatch_token", None)
    if not token:
        raise ValueError("BARENTSWATCH_TOKEN not set in environment")
    return {"Authorization": f"Bearer {token}"}


def get_latest_positions(
    mmsis: list[int] | None = None,
) -> dict[str, Any]:
    """Get latest positions for all vessels or specific MMSIs.

    Data covers Norwegian economic zone only.
    No fishing vessels under 15m, no leisure craft under 45m.

    Args:
        mmsis: Optional list of MMSI numbers to filter.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/latest/combined"

    if mmsis:
        payload = {"mmsis": mmsis}
        logger.info("Fetching BarentsWatch latest positions for %d vessels", len(mmsis))
        resp = requests.post(
            url,
            json=payload,
            headers={**_get_auth_headers(), "Content-Type": "application/json"},
            timeout=30,
        )
    else:
        logger.info("Fetching BarentsWatch latest positions (all vessels)")
        resp = requests.get(url, headers=_get_auth_headers(), timeout=60)

    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_vessel_track(
    mmsi: int,
    hours: int = 4,
) -> dict[str, Any]:
    """Get vessel track for the last N hours.

    Args:
        mmsi: Vessel MMSI number.
        hours: Hours of history (max 14 days).

    Returns:
        Raw API response dict.
    """
    url = f"{HISTORIC_URL}/historic/trackslast{hours}hours/{mmsi}"

    logger.info("Fetching BarentsWatch track for MMSI %d (%d hours)", mmsi, hours)
    resp = requests.get(url, headers=_get_auth_headers(), timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _parse_positions(data: dict[str, Any]) -> pl.DataFrame:
    """Parse position data into a DataFrame."""
    if not data:
        return pl.DataFrame()

    features = data.get("features", [])
    if not features:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for feat in features:
        props = feat.get("properties", {})
        geom = feat.get("geometry", {})
        coords = geom.get("coordinates", [None, None])

        mmsi = props.get("mmsi")
        imo = props.get("imo")

        records.append({
            "mmsi": int(mmsi) if mmsi else None,
            "imo": int(imo) if imo else None,
            "vessel_name": props.get("shipname"),
            "vessel_type": props.get("shiptype"),
            "latitude": coords[1] if len(coords) > 1 else None,
            "longitude": coords[0] if len(coords) > 0 else None,
            "sog": props.get("sog"),
            "cog": props.get("cog"),
            "heading": props.get("heading"),
            "nav_status": props.get("navstatus"),
            "draught": props.get("draught"),
            "destination": props.get("destination"),
            "eta": props.get("eta"),
            "timestamp": props.get("timestamp"),
            "flag": props.get("flag"),
            "stream": props.get("stream"),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    for col in ["timestamp", "eta"]:
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


def _parse_track(data: dict[str, Any]) -> pl.DataFrame:
    """Parse vessel track data into a DataFrame."""
    if not data:
        return pl.DataFrame()

    positions = data.get("positions", [])
    if not positions:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for pos in positions:
        records.append({
            "mmsi": pos.get("mmsi"),
            "latitude": pos.get("lat"),
            "longitude": pos.get("lon"),
            "sog": pos.get("sog"),
            "cog": pos.get("cog"),
            "heading": pos.get("heading"),
            "nav_status": pos.get("navstatus"),
            "timestamp": pos.get("timestamp"),
        })

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


def collect_latest_positions(
    mmsis: list[int] | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect latest positions and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_latest_positions(mmsis)
        df = _parse_positions(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No positions returned from BarentsWatch")
            return 0

        logger.info("Writing %d positions from BarentsWatch", df.height)
        count = write_raw(SOURCE, df)
        tc.rows_written = count
        return count


def collect_vessel_track(
    mmsi: int,
    hours: int = 4,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect vessel track and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_vessel_track(mmsi, hours)
        df = _parse_track(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No track data returned for MMSI %d", mmsi)
            return 0

        logger.info("Writing %d track points for MMSI %d", df.height, mmsi)
        count = write_raw(SOURCE, df)
        tc.rows_written = count
        return count
