from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://services9.arcgis.com/weJ1QsnbMYJlCHdG/arcgis/rest/services"

CHOKEPOINTS_URL = f"{BASE_URL}/PortWatch_chokepoints_database/FeatureServer/0/query"
DAILY_CHOKEPOINTS_URL = f"{BASE_URL}/Daily_Chokepoints_Data/FeatureServer/0/query"

SOURCE = "imf_portwatch"

CHOKEPOINT_NAMES = {
    "CHOKEPOINT1": "Suez Canal",
    "CHOKEPOINT2": "Panama Canal",
    "CHOKEPOINT3": "Strait of Malacca",
    "CHOKEPOINT4": "Bab el-Mandeb Strait",
    "CHOKEPOINT5": "Strait of Hormuz",
    "CHOKEPOINT6": "Cape of Good Hope",
    "CHOKEPOINT7": "Turkish Straits (Bosphorus)",
    "CHOKEPOINT8": "Danish Straits",
    "CHOKEPOINT9": "Kiel Canal",
    "CHOKEPOINT10": "Suez Canal (North)",
    "CHOKEPOINT11": "Suez Canal (South)",
    "CHOKEPOINT12": "Bab el-Mandeb (West)",
    "CHOKEPOINT13": "Bab el-Mandeb (East)",
}


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
        chokepoint_ids: List of chokepoint IDs (e.g. ["CHOKEPOINT1", "CHOKEPOINT5"]).
            If None, returns all chokepoints.
        start_date: Start date filter (YYYY-MM-DD).
        end_date: End date filter (YYYY-MM-DD).

    Returns:
        Raw ArcGIS response dict.
    """
    conditions: list[str] = []
    if chokepoint_ids:
        for cp in chokepoint_ids:
            conditions.append(f"portid = '{cp}'")
        where_clause = " OR ".join(conditions)
    else:
        where_clause = "1=1"

    if start_date:
        where_clause += f" AND date >= TIMESTAMP '{start_date} 00:00:00'"

    params: dict[str, Any] = {
        "where": where_clause,
        "outFields": "*",
        "outSR": "4326",
        "f": "json",
        "resultOffset": 0,
    }

    if end_date:
        params["where"] += f" AND date <= TIMESTAMP '{end_date} 23:59:59'"

    logger.info("Fetching PortWatch daily chokepoint data")
    all_features: list[dict[str, Any]] = []
    offset = 0

    while True:
        params["resultOffset"] = offset
        resp = requests.get(DAILY_CHOKEPOINTS_URL, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        features = data.get("features", [])
        all_features.extend(features)

        if len(features) < 1000:
            break
        offset += 1000

    return {"features": all_features}


def _parse_chokepoint_transits(data: dict[str, Any]) -> pl.DataFrame:
    """Parse daily chokepoint transit data into a DataFrame."""
    features = data.get("features", [])
    if not features:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for feat in features:
        attr = feat.get("attributes", {})
        date_ms = attr.get("date")
        if date_ms and isinstance(date_ms, (int, float)):
            transit_date = datetime.fromtimestamp(date_ms / 1000).strftime("%Y-%m-%d")
        else:
            transit_date = ""

        port_id = attr.get("portid", "")
        port_name = CHOKEPOINT_NAMES.get(port_id, attr.get("portname", port_id))

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
