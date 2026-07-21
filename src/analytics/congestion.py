from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl

from src.storage.reader import read_dataset

logger = logging.getLogger(__name__)


def calculate_port_activity(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
) -> pl.DataFrame:
    """Calculate port activity metrics from AIS positions.

    Analyzes vessel positions near port areas to estimate
    arrivals, departures, and dwell times.

    Returns:
        DataFrame with port activity metrics.
    """
    df = read_dataset(
        "ais_positions",
        date_from=date_from,
        date_to=date_to,
        source=source,
    )

    if df.height == 0:
        return pl.DataFrame()

    df = df.filter(pl.col("destination").is_not_null())

    if df.height == 0:
        return pl.DataFrame()

    port_stats = df.group_by("destination").agg(
        [
            pl.col("imo").n_unique().alias("unique_vessels"),
            pl.col("timestamp").count().alias("position_count"),
            pl.col("timestamp").min().alias("first_seen"),
            pl.col("timestamp").max().alias("last_seen"),
            pl.col("sog").mean().alias("avg_speed"),
            pl.col("draught").mean().alias("avg_draught"),
        ]
    )

    port_stats = port_stats.with_columns(
        [
            (pl.col("last_seen") - pl.col("first_seen"))
            .dt.total_seconds()
            .alias("observation_period_seconds"),
            pl.lit(date.today()).alias("partition_date"),
        ]
    )

    return port_stats.sort("position_count", descending=True)


def estimate_congestion(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
    speed_threshold: float = 1.0,
) -> pl.DataFrame:
    """Estimate port congestion based on vessel speed patterns.

    Vessels moving slowly (< speed_threshold knots) near a port
    destination may indicate congestion or waiting.

    Returns:
        DataFrame with congestion estimates per port.
    """
    df = read_dataset(
        "ais_positions",
        date_from=date_from,
        date_to=date_to,
        source=source,
    )

    if df.height == 0:
        return pl.DataFrame()

    df = df.filter(
        pl.col("destination").is_not_null() & pl.col("sog").is_not_null()
    )

    if df.height == 0:
        return pl.DataFrame()

    slow_vessels = df.filter(pl.col("sog") < speed_threshold)

    if slow_vessels.height == 0:
        return pl.DataFrame()

    congestion = slow_vessels.group_by("destination").agg(
        [
            pl.col("imo").n_unique().alias("slow_vessel_count"),
            pl.col("sog").mean().alias("avg_slow_speed"),
            pl.col("draught").mean().alias("avg_draught"),
            pl.col("timestamp").min().alias("first_slow"),
            pl.col("timestamp").max().alias("last_slow"),
        ]
    )

    all_at_port = df.group_by("destination").agg(
        pl.col("imo").n_unique().alias("total_vessels")
    )

    result = congestion.join(all_at_port, on="destination", how="left")

    result = result.with_columns(
        [
            (pl.col("slow_vessel_count") / pl.col("total_vessels")).alias("congestion_ratio"),
            pl.lit(date.today()).alias("partition_date"),
        ]
    )

    return result.sort("congestion_ratio", descending=True)


def get_vessel_dwell_times(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
    speed_threshold: float = 1.0,
) -> pl.DataFrame:
    """Calculate estimated dwell times for vessels at port.

    Groups consecutive slow-speed positions to estimate
    how long vessels have been waiting or berthed.

    Returns:
        DataFrame with dwell time estimates per vessel.
    """
    df = read_dataset(
        "ais_positions",
        date_from=date_from,
        date_to=date_to,
        source=source,
    )

    if df.height == 0:
        return pl.DataFrame()

    df = df.filter(
        pl.col("destination").is_not_null() & pl.col("sog").is_not_null()
    )

    if df.height == 0:
        return pl.DataFrame()

    vessel_col = "imo" if "imo" in df.columns and df["imo"].null_count() < df.height else "mmsi"

    slow_vessels = df.filter(pl.col("sog") < speed_threshold)

    if slow_vessels.height == 0:
        return pl.DataFrame()

    slow_vessels = slow_vessels.sort([vessel_col, "timestamp"])

    dwell_records: list[dict[str, Any]] = []
    for vessel_id in slow_vessels[vessel_col].unique().to_list():
        vessel_df = slow_vessels.filter(pl.col(vessel_col) == vessel_id)
        if vessel_df.height < 2:
            continue

        positions = vessel_df.to_dicts()
        first_pos = positions[0]
        last_pos = positions[-1]

        start_time = first_pos.get("timestamp")
        end_time = last_pos.get("timestamp")

        if start_time and end_time:
            duration = (end_time - start_time).total_seconds()
            dwell_records.append(
                {
                    vessel_col: vessel_id,
                    "vessel_name": first_pos.get("vessel_name"),
                    "destination": first_pos.get("destination"),
                    "dwell_start": start_time,
                    "dwell_end": end_time,
                    "dwell_seconds": duration,
                    "position_count": len(positions),
                    "avg_speed": sum(p.get("sog", 0) for p in positions) / len(positions),
                }
            )

    if not dwell_records:
        return pl.DataFrame()

    return pl.DataFrame(dwell_records).sort("dwell_seconds", descending=True)
