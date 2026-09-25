"""Simple rules a disruption warning has to beat.

A warning is made at an *origin*: the last complete week in the PortWatch
release. Port data comes out on a Tuesday, complete up to the week that ended
9 days earlier, so the release that completes origin week ``O`` (a Monday) comes
out ``RELEASE_LAG_DAYS`` = 15 days after ``O``. Horizon 1 is the week after the
origin (already over on release day), 2 the current week, 3 next week.

Disruption events (GDACS alerts) are live, so a rule may use every event that
had started by release day, including ones after the last week of port data.

The rules, each a score where higher means more likely to drop:

- ``base_rate``: how often the port has had a disruption, up to the origin,
  shrunk toward the rate across all ports so a port with little history
  isn't 0 or 1.
- ``last_z``: how far below normal the origin week was (minus its z-score).
- ``persistence``: flag the port if the origin week was a drop.
- ``gdacs``: flag the port if a known event started in the target week or the
  2 weeks before and either lists the port (RED alerts only) or is centred
  within ``radius_km`` of it. Droughts are left out: they last months.
- ``persistence_or_gdacs``: either flag.

Flags are 0/1, so flagged ports are ranked among themselves (and unflagged
ports below them) by ``base_rate``.
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

HORIZONS = (1, 2, 3)
RELEASE_LAG_DAYS = 15
EVENT_WEEKS = 3  # an event counts for the week it starts and the 2 after
SKIP_EVENT_TYPES = ("DR",)  # droughts
MAX_KM = 800.0
SHRINK_WEEKS = 26
RULES = ["base_rate", "last_z", "persistence", "gdacs", "persistence_or_gdacs"]
FLAG_RULES = ["persistence", "gdacs", "persistence_or_gdacs"]
_EARTH_KM = 6371.0


def prepare_events(raw: pl.DataFrame) -> pl.DataFrame:
    """One row per event from the ``disruption_events`` table, which holds most
    events twice (the PortWatch list, with affected ports, and GeoPulse)."""
    return (
        raw.filter(~pl.col("event_type").is_in(SKIP_EVENT_TYPES))
        .group_by("event_id")
        .agg(
            pl.col("event_type").first(),
            pl.when((pl.col("alert_level").str.to_uppercase() == "RED").any())
            .then(pl.lit("RED"))
            .otherwise(pl.lit("ORANGE"))
            .alias("level"),
            pl.col("from_date").min().dt.date(),
            pl.col("latitude").drop_nulls().first(),
            pl.col("longitude").drop_nulls().first(),
            pl.col("affected_ports")
            .cast(pl.Utf8)
            .drop_nulls()
            .first()
            .str.split(";")
            .list.eval(pl.element().str.strip_chars())
            .alias("ports"),
        )
        .filter(pl.col("from_date").is_not_null())
        .sort("from_date", "event_id")
    )


def _km(lat1: pl.Expr, lon1: pl.Expr, lat2: pl.Expr, lon2: pl.Expr) -> pl.Expr:
    """Great-circle distance (haversine)."""
    dlat = (lat2 - lat1).radians() / 2
    dlon = (lon2 - lon1).radians() / 2
    a = dlat.sin() ** 2 + lat1.radians().cos() * lat2.radians().cos() * dlon.sin() ** 2
    return 2 * _EARTH_KM * a.sqrt().arcsin()


def event_exposure(
    events: pl.DataFrame, profiles: pl.DataFrame, max_km: float = MAX_KM
) -> pl.DataFrame:
    """Each (port, week) an event could affect: port_id, target_week, event_id,
    level, from_date, km (port to event centre) and listed (the event names
    the port). Pairs further than ``max_km`` that don't list the port are
    dropped."""
    ports = profiles.select(
        "port_id", pl.col("latitude").alias("_plat"), pl.col("longitude").alias("_plon")
    ).filter(pl.col("_plat").is_not_null() & pl.col("_plon").is_not_null())
    pairs = (
        events.join(ports, how="cross")
        .with_columns(
            _km(pl.col("latitude"), pl.col("longitude"), pl.col("_plat"), pl.col("_plon"))
            .alias("km"),
            pl.col("ports").list.contains(pl.col("port_id")).fill_null(False).alias("listed"),
        )
        .filter(pl.col("listed") | (pl.col("km") <= max_km))
    )
    start_week = pl.col("from_date").dt.truncate("1w")
    return pl.concat(
        [
            pairs.select(
                "port_id",
                (start_week + pl.duration(weeks=k)).alias("target_week"),
                "event_id",
                "level",
                "from_date",
                "km",
                "listed",
            )
            for k in range(EVENT_WEEKS)
        ]
    ).sort("port_id", "target_week")


def _base_rate() -> pl.Expr:
    """Disruption rate up to and including each week, per port, shrunk toward
    the pooled rate (column ``_pooled``) by ``SHRINK_WEEKS`` weeks' worth."""
    hits = pl.col("is_disruption").cast(pl.Float64).fill_null(0).cum_sum().over("port_id")
    known = pl.col("is_disruption").is_not_null().cast(pl.Float64).cum_sum().over("port_id")
    return (hits + SHRINK_WEEKS * pl.col("_pooled")) / (known + SHRINK_WEEKS)


def _pooled_rate(labels: pl.DataFrame) -> pl.DataFrame:
    return (
        labels.group_by("week_start")
        .agg(
            pl.col("is_disruption").cast(pl.Float64).sum().alias("_hits"),
            pl.col("is_disruption").is_not_null().sum().alias("_known"),
        )
        .sort("week_start")
        .select(
            "week_start",
            (pl.col("_hits").cum_sum() / pl.col("_known").cum_sum())
            .fill_nan(None)
            .fill_null(0.0)
            .alias("_pooled"),
        )
    )


def warning_rows(
    labels: pl.DataFrame,
    exposure: pl.DataFrame,
    origins: list[date] | None = None,
    horizons: tuple[int, ...] = HORIZONS,
    labelled_only: bool = True,
) -> pl.DataFrame:
    """One row per origin x port x horizon whose target week has a label
    (with ``labelled_only=False``, also those still in the future, ``y`` null):
    the target (``y`` = is_disruption, ``big`` = a disruption of 50%+), what
    was known at the origin, and the nearest known event."""
    base = labels.join(_pooled_rate(labels), on="week_start", how="left").with_columns(
        _base_rate().alias("base_rate")
    )
    frames = []
    for h in horizons:
        ahead = [pl.col(c).shift(-h).over("port_id") for c in ("is_disruption", "drop_pct")]
        frames.append(
            base.select(
                pl.col("week_start").alias("origin_week"),
                (pl.col("week_start") + pl.duration(weeks=h)).alias("target_week"),
                pl.lit(h, dtype=pl.Int8).alias("horizon"),
                "port_id",
                pl.when(pl.col("base") >= 50)
                .then(pl.lit("large (50+/wk)"))
                .otherwise(pl.lit("medium (10-50/wk)"))
                .alias("size_band"),
                ahead[0].alias("y"),
                (ahead[0] & (ahead[1] >= 0.5)).alias("big"),
                "base_rate",
                pl.col("z").alias("origin_z"),
                pl.col("is_drop").fill_null(False).alias("origin_drop"),
            )
        )
    rows = pl.concat(frames)
    if labelled_only:
        rows = rows.filter(pl.col("y").is_not_null())
    if origins is not None:
        rows = rows.filter(pl.col("origin_week").is_in(origins))
    return _attach_events(rows, exposure).sort("origin_week", "horizon", "port_id")


def _attach_events(rows: pl.DataFrame, exposure: pl.DataFrame) -> pl.DataFrame:
    """Nearest event known on release day, and whether a known event lists the port."""
    release = pl.col("origin_week") + pl.duration(days=RELEASE_LAG_DAYS)
    known = (
        rows.select("origin_week", "target_week", "port_id")
        .join(exposure, on=["port_id", "target_week"])
        .filter(pl.col("from_date") <= release)
        .group_by("origin_week", "target_week", "port_id")
        .agg(
            pl.col("km").min().alias("event_km"),
            pl.col("listed").any().alias("event_listed"),
        )
    )
    return rows.join(known, on=["origin_week", "target_week", "port_id"], how="left").with_columns(
        pl.col("event_listed").fill_null(False)
    )


def rule_scores(rows: pl.DataFrame, radius_km: float) -> pl.DataFrame:
    """Add a score column per rule in ``RULES``."""
    near = (pl.col("event_km") <= radius_km).fill_null(False)
    gdacs = pl.col("event_listed") | near
    tie_break = pl.col("base_rate") / 2  # < 1, so a flag always ranks first
    return rows.with_columns(
        (-pl.col("origin_z")).fill_null(0.0).alias("last_z"),
        (pl.col("origin_drop").cast(pl.Float64) + tie_break).alias("persistence"),
        (gdacs.cast(pl.Float64) + tie_break).alias("gdacs"),
        ((pl.col("origin_drop") | gdacs).cast(pl.Float64) + tie_break).alias(
            "persistence_or_gdacs"
        ),
    )


def weekly_origins(labels: pl.DataFrame, start: date, end: date | None = None) -> list[date]:
    """Every week from ``start`` (to ``end``) whose furthest horizon is labelled."""
    last = labels.filter(pl.col("is_disruption").is_not_null())["week_start"].max()
    if not isinstance(last, date):
        return []
    stop = last - timedelta(weeks=max(HORIZONS))
    if end is not None:
        stop = min(stop, end)
    weeks = labels["week_start"].unique().sort()
    return weeks.filter((weeks >= start) & (weeks <= stop)).to_list()
