"""Port-to-port trade flow analysis."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl

from src.storage.reader import read_dataset

logger = logging.getLogger(__name__)


def analyze_port_pairs(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
) -> pl.DataFrame:
    """Analyze vessel movements between port pairs.

    Identifies common routes by analyzing destination changes
    and vessel trajectories.

    Returns:
        DataFrame with port-to-port movement counts.
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

    df = df.filter(
        pl.col(vessel_col).is_not_null() & pl.col("destination").is_not_null()
    )

    if df.height == 0:
        return pl.DataFrame()

    df = df.sort([vessel_col, "timestamp"])

    port_pairs: list[dict[str, Any]] = []
    for vessel_id in df[vessel_col].unique().to_list():
        vessel_df = df.filter(pl.col(vessel_col) == vessel_id)
        destinations = vessel_df["destination"].unique().to_list()

        if len(destinations) < 2:
            continue

        for i in range(len(destinations) - 1):
            port_pairs.append(
                {
                    "origin_port": destinations[i],
                    "destination_port": destinations[i + 1],
                    vessel_col: vessel_id,
                }
            )

    if not port_pairs:
        return pl.DataFrame()

    pairs_df = pl.DataFrame(port_pairs)

    route_counts = pairs_df.group_by(["origin_port", "destination_port"]).agg(
        [
            pl.col(vessel_col).n_unique().alias("vessel_count"),
        ]
    )

    return route_counts.sort("vessel_count", descending=True)


def get_vessel_destination_summary(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
) -> pl.DataFrame:
    """Get summary of vessel destinations.

    Returns counts of vessels heading to each destination port.

    Returns:
        DataFrame with destination port summaries.
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

    vessel_col = "imo" if "imo" in df.columns and df["imo"].null_count() < df.height else "mmsi"

    summary = df.group_by("destination").agg(
        [
            pl.col(vessel_col).n_unique().alias("unique_vessels"),
            pl.col("timestamp").count().alias("position_reports"),
            pl.col("sog").mean().alias("avg_speed"),
            pl.col("draught").mean().alias("avg_draught"),
            pl.col("timestamp").min().alias("first_report"),
            pl.col("timestamp").max().alias("last_report"),
        ]
    )

    return summary.sort("unique_vessels", descending=True)


def analyze_vessel_types_by_destination(
    *,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
) -> pl.DataFrame:
    """Analyze vessel types heading to each destination.

    Returns breakdown of vessel types per destination port.

    Returns:
        DataFrame with vessel type distribution per destination.
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
        pl.col("destination").is_not_null() & pl.col("vessel_name").is_not_null()
    )

    if df.height == 0:
        return pl.DataFrame()

    vessel_col = "imo" if "imo" in df.columns and df["imo"].null_count() < df.height else "mmsi"

    vessel_types = read_dataset("vessels")

    if vessel_types.height == 0:
        return pl.DataFrame()

    if "vessel_type" not in vessel_types.columns:
        return pl.DataFrame()

    vessel_type_map = vessel_types.select(
        [pl.col(vessel_col), pl.col("vessel_type")]
    ).unique(subset=[vessel_col])

    df_with_type = df.join(
        vessel_type_map,
        on=vessel_col,
        how="left",
    )

    type_by_dest = df_with_type.group_by(["destination", "vessel_type"]).agg(
        pl.col(vessel_col).n_unique().alias("vessel_count")
    )

    return type_by_dest.sort(["destination", "vessel_count"], descending=[False, True])
