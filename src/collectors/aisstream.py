from __future__ import annotations

import asyncio
import json
import logging
from datetime import date
from typing import Any

import polars as pl

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

SOURCE = "aisstream"
WSS_URL = "wss://stream.aisstream.io/v0/stream"


def _get_api_key() -> str:
    """Get AISStream API key from settings."""
    key: str | None = getattr(settings, "aisstream_api_key", None)
    if not key:
        raise ValueError("AISSTREAM_API_KEY not set in environment")
    return key


def _parse_position_report(message: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a PositionReport AIS message into a flat record."""
    try:
        ais_msg = message.get("Message", {}).get("PositionReport", {})
        metadata = message.get("Metadata", {})

        if not ais_msg:
            return None

        return {
            "mmsi": ais_msg.get("UserID"),
            "latitude": ais_msg.get("Latitude"),
            "longitude": ais_msg.get("Longitude"),
            "sog": ais_msg.get("Sog"),
            "cog": ais_msg.get("Cog"),
            "heading": ais_msg.get("TrueHeading"),
            "nav_status": str(ais_msg.get("NavigationalStatus", "")),
            "vessel_name": metadata.get("ShipName"),
            "timestamp_raw": ais_msg.get("Timestamp"),
        }
    except (KeyError, TypeError):
        return None


def _parse_ship_static(message: dict[str, Any]) -> dict[str, Any] | None:
    """Parse a ShipStaticData AIS message into a flat record."""
    try:
        ais_msg = message.get("Message", {}).get("ShipStaticData", {})
        metadata = message.get("Metadata", {})

        if not ais_msg:
            return None

        dim = ais_msg.get("Dimension", {})
        a = dim.get("A", 0) or 0
        b = dim.get("B", 0) or 0
        c = dim.get("C", 0) or 0
        d = dim.get("D", 0) or 0

        return {
            "mmsi": ais_msg.get("UserID"),
            "imo": ais_msg.get("ImoNumber"),
            "vessel_name": metadata.get("ShipName") or ais_msg.get("Name"),
            "callsign": ais_msg.get("CallSign"),
            "vessel_type": ais_msg.get("Type"),
            "length_m": float(a + b) if (a or b) else None,
            "beam_m": float(c + d) if (c or d) else None,
            "draught": ais_msg.get("MaximumStaticDraught"),
            "destination": ais_msg.get("Destination"),
        }
    except (KeyError, TypeError):
        return None


async def _collect_stream(
    bbox: list[list[list[float]]],
    duration_seconds: int = 60,
    mmsi_filter: list[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Connect to AISStream WebSocket and collect messages.

    Args:
        bbox: Bounding boxes for area subscription.
        duration_seconds: How long to collect before disconnecting.
        mmsi_filter: Optional list of MMSI strings to filter.

    Returns:
        Tuple of (position_records, vessel_records).
    """
    try:
        import websockets
    except ImportError:
        raise ImportError("websockets package required: pip install websockets")

    positions: list[dict[str, Any]] = []
    vessels: list[dict[str, Any]] = []

    api_key = _get_api_key()

    async with websockets.connect(WSS_URL) as websocket:
        subscribe_msg: dict[str, Any] = {
            "APIKey": api_key,
            "BoundingBoxes": bbox,
            "FilterMessageTypes": ["PositionReport", "ShipStaticData"],
        }
        if mmsi_filter:
            subscribe_msg["FiltersShipMMSI"] = mmsi_filter

        await websocket.send(json.dumps(subscribe_msg))
        logger.info("Connected to AISStream, collecting for %ds", duration_seconds)

        async def _receive() -> None:
            async for raw_msg in websocket:
                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                if "error" in msg:
                    logger.error("AISStream error: %s", msg["error"])
                    continue

                msg_type = msg.get("MessageType", "")

                if msg_type == "PositionReport":
                    rec = _parse_position_report(msg)
                    if rec:
                        positions.append(rec)
                elif msg_type == "ShipStaticData":
                    rec = _parse_ship_static(msg)
                    if rec:
                        vessels.append(rec)

        try:
            await asyncio.wait_for(_receive(), timeout=duration_seconds)
        except TimeoutError:
            total = len(positions) + len(vessels)
            logger.info("AISStream collection complete (%d messages)", total)

    return positions, vessels


def collect_stream(
    bbox: list[list[list[float]]],
    duration_seconds: int = 60,
    mmsi_filter: list[str] | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect AIS data from AISStream and write to storage.

    Args:
        bbox: Bounding boxes for area subscription.
        duration_seconds: How long to collect.
        mmsi_filter: Optional MMSI filter list.
        tracker: Optional source tracker.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        positions, vessels = asyncio.run(
            _collect_stream(bbox, duration_seconds, mmsi_filter)
        )

        total_rows = 0

        if positions:
            df_pos = pl.DataFrame(positions)
            today = date.today()
            df_pos = df_pos.with_columns(
                pl.lit(today).alias("partition_date"),
                pl.lit(SOURCE).alias("source"),
            )
            tc.rows_fetched = df_pos.height
            count = write_raw("ais_positions", df_pos)
            total_rows += count
            logger.info("Wrote %d positions from AISStream", count)

        if vessels:
            df_ves = pl.DataFrame(vessels)
            df_ves = df_ves.with_columns(
                pl.lit(SOURCE).alias("source"),
            )
            count = write_raw("vessels", df_ves)
            total_rows += count
            logger.info("Wrote %d vessel records from AISStream", count)

        tc.rows_written = total_rows
        return total_rows
