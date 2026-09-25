"""Model inputs for the disruption warning.

    python -m src.ml.disruption_warning.features --hf   # print the feature table


One row per origin x port x horizon, the same rows as the baselines
(``baselines.warning_rows``), with the target ``y``. Everything is known on
release day: port data up to the origin week, events that had started by
release day, chokepoint transits up to ``CHOKEPOINT_LAG_DAYS`` before it.
Groups:

- ``FORECAST``: the forecasting model's inputs (``port_forecast.features``):
  recent level, trend, volatility, last year, calendar, port traits.
- ``DROP_STATE``: how the port stood at the origin against its own normal
  (the step-1 label's z-score and drop), recent drops, and whether the port
  dropped around the target week a year ago (such drops count as seasonal,
  not disruptions).
- ``EVENTS``: GDACS events that start in the target week or the 2 before,
  known by release day, within ``baselines.MAX_KM`` or naming the port.
- ``CHOKEPOINTS``: traffic through the port's two nearest chokepoints over
  the last 7 days, against a year earlier and against the 28 days before.
- ``REGION``: how many ports within ``REGION_KM`` dropped in the origin week,
  and how many ports dropped worldwide.
"""
from __future__ import annotations

import argparse
from datetime import date

import duckdb
import polars as pl

from src.ml.disruption_warning.baselines import (
    RELEASE_LAG_DAYS,
    _km,
    event_exposure,
    prepare_events,
    warning_rows,
    weekly_origins,
)
from src.ml.disruption_warning.evaluate import average_precision
from src.ml.disruption_warning.labels import label_drops
from src.ml.port_forecast import features as forecast
from src.ml.port_forecast.backtest import hf_connection, hf_table, load_weekly_hf, load_weekly_local
from src.ml.port_forecast.baselines import YEAR, weekly_grid
from src.storage.writer import get_db_path

#: Chokepoint data runs 2 days ahead of port data on release day.
CHOKEPOINT_LAG_DAYS = 2
NEAREST_CHOKEPOINTS = 2
REGION_KM = 500.0
WEEKS_SINCE_CAP = 156

KEYS = ["origin_week", "target_week", "horizon", "port_id"]
FORECAST = [f for f in forecast.FEATURES if f != "horizon"]
DROP_STATE = [
    "origin_z", "z_lag_1", "z_lag_2", "min_z_4", "origin_drop_pct", "origin_drop",
    "weeks_since_drop", "drops_52", "disruptions_52", "base_rate", "noise_ratio",
    "dropped_near_target_last_year",
]
EVENTS = [
    "event_n", "event_n_red", "event_km", "event_red_km", "event_listed",
    "event_days_before_target", "event_type",
]
CHOKEPOINTS = [
    f"choke{i}_{s}"
    for i in range(1, NEAREST_CHOKEPOINTS + 1)
    for s in ("km", "vs_year_ago", "vs_prior_28d")
]
REGION = ["region_ports", "region_drop_share", "region_mean_z", "world_drop_share"]
FEATURES = ["horizon", *FORECAST, *DROP_STATE, *EVENTS, *CHOKEPOINTS, *REGION]
CATEGORICAL = ["continent", "event_type"]
GROUPS = {
    "forecast": ["horizon", *FORECAST],
    "drop state": DROP_STATE,
    "events": EVENTS,
    "chokepoints": CHOKEPOINTS,
    "region": REGION,
}


def drop_state(labels: pl.DataFrame) -> pl.DataFrame:
    """Per port and week (the origin): recent z-scores and drops."""
    z, drop = pl.col("z"), pl.col("is_drop").fill_null(False)
    last_drop = pl.when(drop).then(pl.col("week_start")).forward_fill().over("port_id")
    return labels.select(
        "port_id",
        pl.col("week_start").alias("origin_week"),
        z.shift(1).over("port_id").alias("z_lag_1"),
        z.shift(2).over("port_id").alias("z_lag_2"),
        z.rolling_min(4, min_samples=1).over("port_id").alias("min_z_4"),
        pl.col("drop_pct").alias("origin_drop_pct"),
        ((pl.col("week_start") - last_drop).dt.total_days() // 7)
        .clip(upper_bound=WEEKS_SINCE_CAP)
        .fill_null(WEEKS_SINCE_CAP)
        .alias("weeks_since_drop"),
        drop.cast(pl.Int16).rolling_sum(YEAR, min_samples=1).over("port_id").alias("drops_52"),
        pl.col("is_disruption").fill_null(False).cast(pl.Int16)
        .rolling_sum(YEAR, min_samples=1).over("port_id").alias("disruptions_52"),
        (pl.col("spread") / pl.col("base")).alias("noise_ratio"),
    )


def year_ago_drops(labels: pl.DataFrame) -> pl.DataFrame:
    """Per port and target week: a drop within +-1 week of it a year earlier.
    ``YEAR - 1`` >= the longest horizon + 1, so this is known at the origin."""
    drop = pl.col("is_drop").fill_null(False)
    near = pl.any_horizontal(*[drop.shift(-k).over("port_id") for k in (-1, 0, 1)])
    return labels.select(
        "port_id",
        (pl.col("week_start") + pl.duration(weeks=YEAR)).alias("target_week"),
        near.fill_null(False).alias("dropped_near_target_last_year"),
    )


def event_features(
    rows: pl.DataFrame, events: pl.DataFrame, profiles: pl.DataFrame
) -> pl.DataFrame:
    """Per row: events known by release day that could hit the target week."""
    exposure = event_exposure(events, profiles).join(
        events.select("event_id", "event_type"), on="event_id", how="left"
    )
    release = pl.col("origin_week") + pl.duration(days=RELEASE_LAG_DAYS)
    red = pl.col("level") == "RED"
    return (
        rows.select(KEYS)
        .join(exposure, on=["port_id", "target_week"])
        .filter(pl.col("from_date") <= release)
        .group_by(KEYS)
        .agg(
            pl.len().cast(pl.Int16).alias("event_n"),
            red.sum().cast(pl.Int16).alias("event_n_red"),
            pl.col("km").filter(red).min().alias("event_red_km"),
            (pl.col("target_week") - pl.col("from_date")).dt.total_days().min()
            .alias("event_days_before_target"),
            pl.col("event_type").sort_by("km").first().alias("event_type"),
        )
    )


def chokepoint_state(daily: pl.DataFrame) -> pl.DataFrame:
    """Per chokepoint and day: last 7 days' transits against the same 7 days
    a year (52 weeks) earlier and against the average 7 days in the 28 before."""
    d = daily.sort("chokepoint_id", "transit_date")
    week = pl.col("n_total").cast(pl.Float64).rolling_sum(7, min_samples=7).over("chokepoint_id")
    return (
        d.with_columns(week.alias("_7d"))
        .with_columns(
            pl.col("_7d").shift(YEAR * 7).over("chokepoint_id").alias("_7d_year_ago"),
            (pl.col("n_total").cast(pl.Float64).shift(7).rolling_sum(28, min_samples=28)
             .over("chokepoint_id") / 4).alias("_7d_prior"),
        )
        .select(
            "chokepoint_id",
            pl.col("transit_date").alias("day"),
            (pl.col("_7d") / pl.when(pl.col("_7d_year_ago") > 0).then(pl.col("_7d_year_ago")) - 1)
            .alias("vs_year_ago"),
            (pl.col("_7d") / pl.when(pl.col("_7d_prior") > 0).then(pl.col("_7d_prior")) - 1)
            .alias("vs_prior_28d"),
        )
    )


def nearest_chokepoints(daily: pl.DataFrame, profiles: pl.DataFrame) -> pl.DataFrame:
    """port_id, rank (1 = nearest), chokepoint_id, km."""
    points = daily.group_by("chokepoint_id").agg(
        pl.col("latitude").first().alias("_clat"), pl.col("longitude").first().alias("_clon")
    )
    ports = profiles.select("port_id", "latitude", "longitude").drop_nulls()
    return (
        ports.join(points, how="cross")
        .with_columns(
            _km(pl.col("latitude"), pl.col("longitude"), pl.col("_clat"), pl.col("_clon"))
            .alias("km")
        )
        .sort("port_id", "km")
        .with_columns(pl.col("km").rank("ordinal").over("port_id").cast(pl.Int8).alias("rank"))
        .filter(pl.col("rank") <= NEAREST_CHOKEPOINTS)
        .select("port_id", "rank", "chokepoint_id", "km")
    )


def chokepoint_features(
    daily: pl.DataFrame, profiles: pl.DataFrame, origins: list[date]
) -> pl.DataFrame:
    """Per origin and port: the nearest chokepoints, as of release day minus
    ``CHOKEPOINT_LAG_DAYS``."""
    lag = RELEASE_LAG_DAYS - CHOKEPOINT_LAG_DAYS
    days = pl.DataFrame({"origin_week": origins}, schema={"origin_week": pl.Date}).with_columns(
        (pl.col("origin_week") + pl.duration(days=lag)).alias("day")
    )
    state = chokepoint_state(daily).join(days, on="day")
    long = nearest_chokepoints(daily, profiles).join(state, on="chokepoint_id")
    wide = long.pivot(
        on="rank",
        index=["origin_week", "port_id"],
        values=["km", "vs_year_ago", "vs_prior_28d"],
        sort_columns=True,
    ).rename(
        {
            f"{v}_{i}": f"choke{i}_{v}"
            for v in ("km", "vs_year_ago", "vs_prior_28d")
            for i in range(1, NEAREST_CHOKEPOINTS + 1)
        },
        strict=False,
    )
    missing = [pl.lit(None, pl.Float64).alias(c) for c in CHOKEPOINTS if c not in wide.columns]
    return wide.with_columns(missing).select("origin_week", "port_id", *CHOKEPOINTS)


def region_features(
    labels: pl.DataFrame, profiles: pl.DataFrame, origins: list[date]
) -> pl.DataFrame:
    """Per origin and port: labelled ports within ``REGION_KM`` (not itself),
    the share of them that dropped in the origin week and their mean z; and the
    share of all labelled ports that dropped."""
    ports = profiles.select("port_id", "latitude", "longitude").drop_nulls()
    pairs = (
        ports.join(ports.rename(lambda c: f"nb_{c}"), how="cross")
        .filter(pl.col("port_id") != pl.col("nb_port_id"))
        .filter(
            _km(pl.col("latitude"), pl.col("longitude"), pl.col("nb_latitude"),
                pl.col("nb_longitude")) <= REGION_KM
        )
        .select("port_id", "nb_port_id")
    )
    week = (
        labels.filter(pl.col("week_start").is_in(origins) & pl.col("eligible"))
        .select(
            pl.col("week_start").alias("origin_week"),
            pl.col("port_id").alias("nb_port_id"),
            pl.col("is_drop").cast(pl.Float64).alias("_drop"),
            "z",
        )
    )
    world = week.group_by("origin_week").agg(pl.col("_drop").mean().alias("world_drop_share"))
    region = (
        pairs.join(week, on="nb_port_id")
        .group_by("origin_week", "port_id")
        .agg(
            pl.len().cast(pl.Int16).alias("region_ports"),
            pl.col("_drop").mean().alias("region_drop_share"),
            pl.col("z").mean().alias("region_mean_z"),
        )
    )
    grid = pl.DataFrame({"origin_week": origins}, schema={"origin_week": pl.Date}).join(
        ports.select("port_id"), how="cross"
    )
    return (
        grid.join(region, on=["origin_week", "port_id"], how="left")
        .join(world, on="origin_week", how="left")
        .with_columns(pl.col("region_ports").fill_null(0))
    )


def build_features(
    weekly: pl.DataFrame,
    raw_events: pl.DataFrame,
    profiles: pl.DataFrame,
    chokepoints: pl.DataFrame,
    origins: list[date],
    labelled_only: bool = True,
) -> pl.DataFrame:
    """KEYS, the target (``y``, ``big``, ``size_band``) and FEATURES. With
    ``labelled_only=False`` rows whose target is still ahead are kept (``y``
    null): that is what a live warning scores."""
    grid = weekly_grid(weekly)
    labels = label_drops(grid)
    events = prepare_events(raw_events)
    rows = warning_rows(labels, event_exposure(events, profiles), origins,
                        labelled_only=labelled_only)
    fx = forecast.build_features(grid, profiles, origins, require_actual=False).drop(
        "actual", "target"
    )
    out = (
        rows.join(fx, on=["origin_week", "target_week", "port_id", "horizon"], how="left")
        .join(drop_state(labels), on=["origin_week", "port_id"], how="left")
        .join(year_ago_drops(labels), on=["target_week", "port_id"], how="left")
        .join(event_features(rows, events, profiles), on=KEYS, how="left")
        .join(chokepoint_features(chokepoints, profiles, origins),
              on=["origin_week", "port_id"], how="left")
        .join(region_features(labels, profiles, origins), on=["origin_week", "port_id"],
              how="left")
        .with_columns(
            pl.col("event_n", "event_n_red").fill_null(0),
            pl.col("dropped_near_target_last_year").fill_null(False),
            pl.col("event_type").cast(pl.Categorical),
        )
    )
    return out.select(*KEYS, "y", "big", "size_band", *FEATURES[1:]).sort(
        "origin_week", "horizon", "port_id"
    )


EVENT_COLUMNS = "event_id, event_type, alert_level, from_date, latitude, longitude, affected_ports"
CHOKEPOINT_COLUMNS = "chokepoint_id, transit_date, latitude, longitude, n_total"


def load_inputs(hf: bool) -> tuple[pl.DataFrame, ...]:
    """Weekly calls, raw events, port profiles and daily chokepoint transits."""
    queries = {
        "disruption_events": EVENT_COLUMNS,
        "port_profiles": "*",
        "chokepoint_daily": CHOKEPOINT_COLUMNS,
    }
    if hf:
        conn = hf_connection()
        tables = [
            conn.execute(f"SELECT {cols} FROM read_parquet({hf_table(t)})").pl()
            for t, cols in queries.items()
        ]
        return (load_weekly_hf(), *tables)
    with duckdb.connect(str(get_db_path()), read_only=True) as conn:
        tables = [conn.execute(f"SELECT {cols} FROM {t}").pl() for t, cols in queries.items()]
    return (load_weekly_local(), *tables)


def single_feature_ap(rows: pl.DataFrame, split: date) -> pl.DataFrame:
    """Each numeric feature used alone as a score. Its direction (high or low
    = drop) is picked on origins before ``split``, then scored after it.
    Missing values rank last."""
    before = rows.filter(pl.col("origin_week") < split)
    after = rows.filter(pl.col("origin_week") >= split)
    out = []
    for f in FEATURES:
        if f in CATEGORICAL:
            continue

        def ap(part: pl.DataFrame, sign: int, f: str = f) -> float | None:
            s = (pl.col(f).cast(pl.Float64) * sign).fill_nan(None)
            return average_precision(
                part.select(s.fill_null(s.min() - 1).alias("_s"), "y"), "_s"
            )

        up, down = ap(before, 1) or 0.0, ap(before, -1) or 0.0
        sign = 1 if up >= down else -1
        out.append({"feature": f, "higher_means_drop": sign == 1, "ap": ap(after, sign)})
    return pl.DataFrame(out)


def describe(rows: pl.DataFrame, split: date) -> pl.DataFrame:
    """One row per feature: group, share filled in, median when a disruption
    followed and when not, and its average precision used alone."""
    yes, no = rows.filter(pl.col("y")), rows.filter(~pl.col("y"))

    def med(part: pl.DataFrame, f: str) -> str:
        col = part[f]
        if f in CATEGORICAL:
            top = col.drop_nulls().mode()
            return str(top[0]) if len(top) else "-"
        v = col.cast(pl.Float64).median()
        return "-" if not isinstance(v, float) else f"{v:.3g}"

    table = pl.DataFrame(
        [
            {
                "group": g,
                "feature": f,
                "filled": 1 - rows[f].null_count() / rows.height,
                "median_if_drop": med(yes, f),
                "median_if_not": med(no, f),
            }
            for g, fs in GROUPS.items()
            for f in fs
        ]
    )
    return table.join(single_feature_ap(rows, split), on="feature", how="left")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--hf", action="store_true", help="read the tables from HF")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2020, 1, 6))
    parser.add_argument("--split", type=date.fromisoformat, default=date(2023, 1, 2))
    parser.add_argument("--out", help="also write the rows to this parquet file")
    args = parser.parse_args(argv)

    weekly, events, profiles, chokepoints = load_inputs(args.hf)
    origins = weekly_origins(label_drops(weekly_grid(weekly)), args.start)
    rows = build_features(weekly, events, profiles, chokepoints, origins)
    after = rows.filter(pl.col("origin_week") >= args.split)
    hits = int(after["y"].sum())
    print(
        f"{rows.height:,} rows ({len(origins)} weekly origins from {origins[0]} to "
        f"{origins[-1]}), {len(FEATURES)} features; from {args.split}: {after.height:,} rows, "
        f"{hits:,} disruptions ({hits / after.height:.2%})"
    )
    sample = pl.concat(
        [after.filter(pl.col("y")).sample(3, seed=1), after.filter(~pl.col("y")).sample(2, seed=1)]
    ).select(
        pl.concat_str(
            "port_id", pl.col("origin_week").cast(pl.Utf8), pl.format("h{}", "horizon"),
            separator=" ",
        ).alias("row"),
        *[pl.col(c).cast(pl.Utf8) for c in ["y", *FEATURES]],
    )
    with pl.Config(
        tbl_rows=200, tbl_cols=20, float_precision=3, tbl_hide_dataframe_shape=True,
        tbl_formatting="ASCII_MARKDOWN", tbl_hide_column_data_types=True, fmt_str_lengths=40,
    ):
        print(f"\nFeatures (ap = the feature alone as a score, origins from {args.split})")
        print(describe(rows, args.split))
        print("\nSample rows (3 disruptions, 2 not)")
        print(sample.transpose(include_header=True, header_name="feature", column_names="row"))
    if args.out:
        rows.write_parquet(args.out)


if __name__ == "__main__":
    main()
