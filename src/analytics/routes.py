from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl

from src.storage.reader import read_dataset

logger = logging.getLogger(__name__)


def build_vessel_tracks(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
) -> pl.DataFrame:
    """Build vessel tracks from AIS position sequences.

    Groups positions by vessel (IMO or MMSI) and orders by timestamp
    to create track segments.

    Returns:
        DataFrame with vessel tracks including start/end positions,
        duration, and distance.
    """
    df = read_dataset(
        "ais_positions",
        date_from=date_from,
        date_to=date_to,
        source=source,
    )

    if df.height == 0:
        return pl.DataFrame()

    vessel_col = "imo" if "imo" in df.columns and df["imo"].null_count() < df.height else "mmsi"

    df = df.filter(pl.col(vessel_col).is_not_null())

    if df.height == 0:
        return pl.DataFrame()

    df = df.sort([vessel_col, "timestamp"])

    tracks = df.group_by(vessel_col).agg(
        [
            pl.col("vessel_name").first().alias("vessel_name"),
            pl.col("latitude").first().alias("start_lat"),
            pl.col("longitude").first().alias("start_lon"),
            pl.col("latitude").last().alias("end_lat"),
            pl.col("longitude").last().alias("end_lon"),
            pl.col("timestamp").first().alias("start_time"),
            pl.col("timestamp").last().alias("end_time"),
            pl.col("sog").mean().alias("avg_speed"),
            pl.col("sog").max().alias("max_speed"),
            pl.col("latitude").count().alias("position_count"),
            pl.col("destination").last().alias("reported_destination"),
        ]
    )

    tracks = tracks.with_columns(
        [
            (pl.col("end_time") - pl.col("start_time"))
            .dt.total_seconds()
            .alias("duration_seconds"),
            pl.lit(date.today()).alias("partition_date"),
        ]
    )

    return tracks


def build_route_segments(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
    min_positions: int = 2,
) -> pl.DataFrame:
    """Build route segments between consecutive positions.

    Returns consecutive position pairs with calculated bearing
    and distance for route analysis.
    """
    df = read_dataset(
        "ais_positions",
        date_from=date_from,
        date_to=date_to,
        source=source,
    )

    if df.height == 0:
        return pl.DataFrame()

    vessel_col = "imo" if "imo" in df.columns and df["imo"].null_count() < df.height else "mmsi"

    df = df.filter(pl.col(vessel_col).is_not_null())
    df = df.sort([vessel_col, "timestamp"])

    segments: list[dict[str, Any]] = []
    for vessel_id in df[vessel_col].unique().to_list():
        vessel_df = df.filter(pl.col(vessel_col) == vessel_id)
        if vessel_df.height < min_positions:
            continue

        positions = vessel_df.to_dicts()
        for i in range(len(positions) - 1):
            p1 = positions[i]
            p2 = positions[i + 1]

            lat1, lon1 = p1.get("latitude"), p1.get("longitude")
            lat2, lon2 = p2.get("latitude"), p2.get("longitude")

            if None in (lat1, lon1, lat2, lon2):
                continue

            segments.append(
                {
                    vessel_col: vessel_id,
                    "vessel_name": p1.get("vessel_name"),
                    "lat1": lat1,
                    "lon1": lon1,
                    "lat2": lat2,
                    "lon2": lon2,
                    "time1": p1.get("timestamp"),
                    "time2": p2.get("timestamp"),
                    "sog1": p1.get("sog"),
                    "sog2": p2.get("sog"),
                    "cog1": p1.get("cog"),
                    "cog2": p2.get("cog"),
                }
            )

    if not segments:
        return pl.DataFrame()

    return pl.DataFrame(segments)


def get_active_vessels(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
) -> pl.DataFrame:
    """Get summary of active vessels in the dataset.

    Returns vessel count, position count, and activity metrics.
    """
    df = read_dataset(
        "ais_positions",
        date_from=date_from,
        date_to=date_to,
        source=source,
    )

    if df.height == 0:
        return pl.DataFrame()

    vessel_col = "imo" if "imo" in df.columns and df["imo"].null_count() < df.height else "mmsi"

    summary = df.group_by(vessel_col).agg(
        [
            pl.col("vessel_name").first().alias("vessel_name"),
            pl.col("timestamp").count().alias("position_count"),
            pl.col("timestamp").min().alias("first_seen"),
            pl.col("timestamp").max().alias("last_seen"),
            pl.col("sog").mean().alias("avg_speed"),
            pl.col("latitude").mean().alias("avg_lat"),
            pl.col("longitude").mean().alias("avg_lon"),
        ]
    )

    return summary.sort("position_count", descending=True)
