"""Baseline forecasts of weekly port calls: the bar a model has to clear.

A forecast is made at an *origin*: the last complete week known at the time.
Horizon ``h`` forecasts the week ``h`` weeks after the origin. Every baseline
uses only weeks up to the origin, so a backtest can't see the future:

- ``last_year``: the target week's value 52 weeks earlier (364 days, so the
  weeks line up by weekday).
- ``last_year_scaled``: ``last_year`` times how busy the port has been lately
  compared with a year before (last 8 weeks vs the same 8 weeks a year
  earlier, clipped to 0.2-5 so a near-empty port can't blow it up).
- ``last_4``: the average of the last 4 known weeks.
- ``seasonal_avg``: the average of the target week in each of the last 3 years.

Partial weeks (``is_complete_week`` false) and weeks missing from the feed count
as unknown, never as zero.
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

BASELINES = ["last_year", "last_year_scaled", "last_4", "seasonal_avg"]
HORIZONS = (1, 2, 3, 4)
YEAR = 52  # weeks



def _as_date(value: object) -> date:
    # Series.min()/max() are typed as any scalar; on a Date column they're dates.
    if not isinstance(value, date):
        raise TypeError(f"expected a date, got {value!r}")
    return value


def weekly_grid(weekly: pl.DataFrame, value: str = "portcalls") -> pl.DataFrame:
    """Every port x every week from the first to the last, with ``y`` the value
    of complete weeks and null otherwise. Rows are sorted by port, then week,
    with no gaps, so a shift of ``n`` within a port is exactly ``n`` weeks."""
    first, last = _as_date(weekly["week_start"].min()), _as_date(weekly["week_start"].max())
    weeks = pl.DataFrame(
        {"week_start": pl.date_range(first, last, interval="7d", eager=True)}
    )
    ports = weekly.select("port_id").unique()
    known = weekly.select(
        "port_id",
        "week_start",
        pl.when(pl.col("is_complete_week"))
        .then(pl.col(value).cast(pl.Float64))
        .alias("y"),
    )
    return (
        ports.join(weeks, how="cross")
        .join(known, on=["port_id", "week_start"], how="left")
        .sort("port_id", "week_start")
    )


def _trailing_mean(col: pl.Expr, weeks: int) -> pl.Expr:
    """Mean of the non-null values in the last ``weeks`` rows, per port."""
    total = col.fill_null(0).rolling_sum(weeks, min_samples=1)
    count = col.is_not_null().cast(pl.Float64).rolling_sum(weeks, min_samples=1)
    return (total / pl.when(count > 0).then(count)).over("port_id")


def _size_band(avg: pl.Expr) -> pl.Expr:
    """From the port's trailing 52-week average calls a week, at the origin."""
    return (
        pl.when(avg.is_null())
        .then(pl.lit(None, dtype=pl.Utf8))
        .when(avg < 5)
        .then(pl.lit("small (<5/wk)"))
        .when(avg < 50)
        .then(pl.lit("medium (5-50/wk)"))
        .otherwise(pl.lit("large (50+/wk)"))
    )


def baseline_forecasts(
    grid: pl.DataFrame,
    origins: list[date] | None = None,
    horizons: tuple[int, ...] = HORIZONS,
) -> pl.DataFrame:
    """One row per origin x port x horizon with the actual value and each
    baseline's forecast. Rows whose actual is unknown are dropped."""
    y = pl.col("y")
    base = grid.with_columns(_trailing_mean(y, 8).alias("_recent_8")).with_columns(
        # Looked up at the target week: all of these reach back >= 52 weeks,
        # so for horizons up to 52 they only touch weeks known at the origin.
        y.shift(YEAR).over("port_id").alias("_ly"),
        pl.mean_horizontal(
            *[y.shift(YEAR * k).over("port_id") for k in (1, 2, 3)]
        ).alias("_seasonal"),
        # Known at the origin week.
        _trailing_mean(y, 4).alias("_last_4"),
        (pl.col("_recent_8") / pl.col("_recent_8").shift(YEAR).over("port_id"))
        .clip(0.2, 5.0)
        .alias("_ratio"),
        _trailing_mean(y, YEAR).alias("_avg_52"),
    )
    frames = []
    for h in horizons:
        at_target = [
            pl.col(c).shift(-h).over("port_id") for c in ("y", "_ly", "_seasonal")
        ]
        frames.append(
            base.select(
                pl.col("week_start").alias("origin_week"),
                (pl.col("week_start") + pl.duration(weeks=h)).alias("target_week"),
                pl.lit(h, dtype=pl.Int8).alias("horizon"),
                "port_id",
                _size_band(pl.col("_avg_52")).alias("size_band"),
                at_target[0].alias("actual"),
                at_target[1].alias("last_year"),
                (at_target[1] * pl.col("_ratio").fill_nan(None).fill_null(1.0)).alias(
                    "last_year_scaled"
                ),
                pl.col("_last_4").alias("last_4"),
                at_target[2].alias("seasonal_avg"),
            )
        )
    out = pl.concat(frames).filter(pl.col("actual").is_not_null())
    if origins is not None:
        out = out.filter(pl.col("origin_week").is_in(origins))
    return out.sort("origin_week", "horizon", "port_id")


def backtest_origins(
    grid: pl.DataFrame, start: date, every_weeks: int = 4, max_horizon: int = 4
) -> list[date]:
    """Origins every ``every_weeks`` weeks from ``start``, stopping where the
    furthest horizon would run past the data."""
    last = _as_date(grid.filter(pl.col("y").is_not_null())["week_start"].max())
    weeks = grid["week_start"].unique().sort()
    candidates = weeks.filter(
        (weeks >= start) & (weeks <= last - timedelta(weeks=max_horizon))
    )
    return candidates.gather_every(every_weeks).to_list()
