"""Collect chokepoint transit data from IMF PortWatch."""
from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime
from typing import Any

import polars as pl
import requests

from src.collectors.portwatch_ports import query_all
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"

CHOKEPOINTS_URL = f"{BASE_URL}/PortWatch_chokepoints_database/FeatureServer/0/query"
DAILY_CHOKEPOINTS_URL = f"{BASE_URL}/Daily_Chokepoints_Data/FeatureServer/0/query"

SOURCE = "imf_portwatch"

# Chokepoint ids are "chokepoint1".."chokepoint28"; names come from each
# row's own portname. (A hardcoded id -> name table used to live here; its
# uppercase keys never matched, and its names were wrong -- it called
# chokepoint3 Malacca, which PortWatch lists as the Bosporus.)
_CHOKEPOINT_ID = re.compile(r"^[A-Za-z0-9_]+$")


def get_chokepoint_info() -> dict[str, Any]:
    """Get metadata for all chokepoints from PortWatch."""
    params: dict[str, Any] = {
        "where": "1=1",
        "outFields": "*",
        "outSR": "4326",
        "f": "json",
    }
    logger.info("Fetching PortWatch chokepoint info")
    resp = requests.get(CHOKEPOINTS_URL, params=params, timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_daily_chokepoint_data(
    chokepoint_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> dict[str, Any]:
    """Get daily transit counts and capacity for chokepoints.

    Args:
        chokepoint_ids: List of chokepoint IDs (e.g. ["chokepoint1", "chokepoint6"]).
            If None, returns all chokepoints.
        start_date: Start date filter (YYYY-MM-DD).
        end_date: End date filter (YYYY-MM-DD).

    Returns:
        Raw ArcGIS response dict.
    """
    conditions: list[str] = []
    if chokepoint_ids:
        for cp in chokepoint_ids:
            if not _CHOKEPOINT_ID.match(cp):
                raise ValueError(f"Invalid chokepoint id: {cp!r}")
        # Bracketed: SQL's AND binds tighter than OR, so without them the
        # date filter below applied only to the last id.
        conditions.append(
            "(" + " OR ".join(f"portid = '{cp}'" for cp in chokepoint_ids) + ")"
        )

    for label, value, clock, op in (
        ("start_date", start_date, "00:00:00", ">="),
        ("end_date", end_date, "23:59:59", "<="),
    ):
        if value:
            if not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
                raise ValueError(f"Invalid {label} format: {value!r}. Must be YYYY-MM-DD.")
            conditions.append(f"date {op} TIMESTAMP '{value} {clock}'")

    where = " AND ".join(conditions) or "1=1"
    logger.info("Fetching PortWatch daily chokepoint data")
    # query_all orders by ObjectId and follows exceededTransferLimit; unordered
    # offset paging can return overlapping or skipped rows between pages.
    return {"features": query_all(DAILY_CHOKEPOINTS_URL, where=where)}


def _parse_chokepoint_transits(data: dict[str, Any]) -> pl.DataFrame:
    """Parse daily chokepoint transit data into a DataFrame."""
    features = data.get("features", [])
    if not features:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for feat in features:
        attr = feat.get("attributes", {})
        date_val = attr.get("date")
        if isinstance(date_val, str) and date_val:
            # ArcGIS returns ISO date strings ("2024-06-01")
            transit_date = date_val[:10]
        elif isinstance(date_val, (int, float)):
            # Fallback: some ArcGIS layers return epoch milliseconds
            transit_date = datetime.fromtimestamp(date_val / 1000, tz=UTC).strftime("%Y-%m-%d")
        else:
            transit_date = ""

        port_id = attr.get("portid", "")
        port_name = attr.get("portname") or port_id

        records.append({
            "transit_date": transit_date,
            "chokepoint_id": port_id,
            "chokepoint_name": port_name,
            "n_container": attr.get("n_container", 0),
            "n_dry_bulk": attr.get("n_dry_bulk", 0),
            "n_general_cargo": attr.get("n_general_cargo", 0),
            "n_roro": attr.get("n_roro", 0),
            "n_tanker": attr.get("n_tanker", 0),
            "n_cargo": attr.get("n_cargo", 0),
            "n_total": attr.get("n_total", 0),
            "capacity_container": attr.get("capacity_container", 0),
            "capacity_dry_bulk": attr.get("capacity_dry_bulk", 0),
            "capacity_general_cargo": attr.get("capacity_general_cargo", 0),
            "capacity_roro": attr.get("capacity_roro", 0),
            "capacity_tanker": attr.get("capacity_tanker", 0),
            "capacity_cargo": attr.get("capacity_cargo", 0),
            "capacity": attr.get("capacity", 0),
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


def collect_chokepoint_transits(
    chokepoint_ids: list[str] | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect chokepoint transit data and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_daily_chokepoint_data(
            chokepoint_ids=chokepoint_ids,
            start_date=start_date,
            end_date=end_date,
        )
        df = _parse_chokepoint_transits(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No PortWatch transit data returned")
            return 0

        logger.info("Writing %d PortWatch transit records", df.height)
        count = write_raw(SOURCE, df, table_name="chokepoint_transits")
        tc.rows_written = count
        return count
