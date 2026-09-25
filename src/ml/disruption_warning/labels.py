"""Which port-weeks count as a significant traffic drop.

A complete week is a **drop** when its port calls are both:

- at least ``MIN_DROP`` (30%) below the **median** of the previous
  ``BASE_WEEKS`` (13) complete weeks, and
- at least ``MIN_Z`` (2.5) spreads below it, where the spread is the larger of
  the interquartile range of those weeks / 1.349 (a standard deviation that one
  or two odd weeks can't inflate) and sqrt(median), the spread pure chance
  gives a count.

The second rule keeps count noise out: a port averaging 20 calls has roughly a
1 in 10 chance of 14 or fewer in a given week with nothing wrong, and a 30% rule
alone would call that a disruption.

Median and IQR, not mean and standard deviation, so the first weeks of a
collapse don't drag the baseline down and hide the weeks after. A fall that
lasts more than ~6 of the 13 weeks becomes the new normal and stops counting;
this label is about sharp drops, not lasting shifts.

A drop that also happened within a week of the same week a year earlier
(Christmas-New Year, Golden Week...) is **seasonal**: a calendar predicts it.
``is_disruption`` is a drop that isn't seasonal, and is the target for early
warning. Without a labelled week a year earlier (the port's first year) both
are null: it can't be told apart.

Only ports whose median is at least ``MIN_BASE`` calls a week, with at least
``MIN_KNOWN`` of the 13 base weeks known, are **eligible**; other rows get null
labels rather than "no drop".
"""
from __future__ import annotations

import polars as pl

from src.ml.port_forecast.baselines import YEAR

BASE_WEEKS = 13
MIN_KNOWN = 10
MIN_BASE = 10.0
MIN_DROP = 0.30
MIN_Z = 2.5
_IQR_TO_SD = 1.349  # IQR of a normal distribution in standard deviations


def label_drops(grid: pl.DataFrame) -> pl.DataFrame:
    """Add ``base``, ``spread``, ``z``, ``drop_pct``, ``eligible`` and
    ``is_drop`` to a weekly grid (see ``port_forecast.baselines.weekly_grid``).
    The baseline covers the 13 weeks *before* each week, never the week itself."""
    prior = pl.col("y").shift(1)

    def q(quantile: float) -> pl.Expr:
        return prior.rolling_quantile(
            quantile, window_size=BASE_WEEKS, min_samples=MIN_KNOWN, interpolation="linear"
        ).over("port_id")

    out = grid.with_columns(q(0.5).alias("base"), q(0.25).alias("_q1"), q(0.75).alias("_q3"))
    out = out.with_columns(
        pl.max_horizontal(
            (pl.col("_q3") - pl.col("_q1")) / _IQR_TO_SD, pl.col("base").sqrt()
        ).alias("spread")
    )
    eligible = pl.col("y").is_not_null() & (pl.col("base") >= MIN_BASE)
    eligible = eligible.fill_null(False)
    z = (pl.col("y") - pl.col("base")) / pl.col("spread")
    drop_pct = 1 - pl.col("y") / pl.col("base")
    return out.with_columns(
        eligible.alias("eligible"),
        pl.when(eligible).then(z).alias("z"),
        pl.when(eligible).then(drop_pct).alias("drop_pct"),
        pl.when(eligible).then((drop_pct >= MIN_DROP) & (z <= -MIN_Z)).alias("is_drop"),
    ).drop("_q1", "_q3").with_columns(_seasonal_flags())


def _seasonal_flags() -> list[pl.Expr]:
    def year_ago(col: str) -> pl.Expr:
        return pl.any_horizontal(
            *[pl.col(col).fill_null(False).shift(YEAR + k).over("port_id") for k in (-1, 0, 1)]
        ).fill_null(False)

    known = pl.col("eligible") & year_ago("eligible")
    last_year = year_ago("is_drop")
    return [
        pl.when(known).then(pl.col("is_drop") & last_year).alias("is_seasonal"),
        pl.when(known).then(pl.col("is_drop") & ~last_year).alias("is_disruption"),
    ]


def drop_episodes(labels: pl.DataFrame) -> pl.DataFrame:
    """Runs of consecutive drop weeks per port: start, weeks, deepest drop."""
    flagged = labels.filter(pl.col("eligible")).sort("port_id", "week_start")
    run_id = (
        (pl.col("is_drop") != pl.col("is_drop").shift(1)).fill_null(True).cum_sum().over("port_id")
    )
    return (
        flagged.with_columns(run_id.alias("_run"))
        .filter(pl.col("is_drop"))
        .group_by("port_id", "_run")
        .agg(
            pl.col("week_start").min().alias("start"),
            pl.len().alias("weeks"),
            pl.col("drop_pct").max().alias("deepest"),
            pl.col("base").first().alias("base"),
        )
        .drop("_run")
        .sort("start", "port_id")
    )
