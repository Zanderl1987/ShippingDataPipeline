"""Forecast a marketing year's total US exports from how far booking has got.

Two simple estimates are available at any week of a season:

- **last season**: the total shipped last season. Exports change slowly, so
  this is hard to beat early on.
- **pace**: tons committed so far divided by the share of a season's exports
  that has usually been committed by this week (``grain_export_pace``). Early
  in the season that share is small and the estimate swings widely; by spring
  it is within a few percent.

The forecast blends them: ``log(forecast) = (1 - a) * log(last season) +
a * log(pace)``. The weight ``a`` (0 to 1) is fit separately for each crop and
each week of the season, on that week and the two either side, in seasons
that had finished before the one being forecast. One number per week cannot
overfit 20 seasons the way a richer model does; richer models were tried and
did worse out of sample (README.md).

Corn uses the pace estimate unblended (``FIXED_WEIGHTS``). Corn's season
totals swing far more than soybeans' (+110% after the 2012 drought, -62% the
season after), the fitted weight leaned on last season to hedge against those
swings, and over 2008-2025 that made it worse than plain pace at every stage
of the season.

The 80% range is built from the forecast's own misses on earlier seasons, at
the same week of the season give or take ``WEEK_WINDOW``, each made before
that season's outcome was known. With fewer than ``MIN_RANGE_SEASONS`` such
seasons there is no range. The range is not centred on the forecast: if past
forecasts at this week mostly ran low, it sits mostly above.

Everything is plain Python so it runs in curation without the ``ml`` extra.
"""
from __future__ import annotations

import logging
import math
from typing import Any

import duckdb
import polars as pl

logger = logging.getLogger(__name__)

TABLE = "grain_export_forecast"
#: Training rows come from this many weeks either side of the forecast week.
WEEK_WINDOW = 2
#: Finished seasons needed before a forecast is made.
MIN_TRAIN_SEASONS = 8
#: Earlier out-of-sample forecasts needed before a range is given.
MIN_RANGE_SEASONS = 5
WEIGHT_GRID = [i / 20 for i in range(21)]
RANGE_QUANTILES = (0.1, 0.9)
#: Crops whose weight is fixed instead of fit (1.0 = pace estimate alone).
FIXED_WEIGHTS = {"Corn": 1.0}
#: First week of the season from which the forecast has beaten simply
#: repeating last season's total (backtest 2008-2025; README.md). Corn's
#: bookings in September and October say little about its final total.
RELIABLE_FROM_WEEK = {"Corn": 9, "Soybeans": 1}

_SCHEMA = {
    "commodity": pl.Utf8, "marketing_year": pl.Utf8, "my_week": pl.Int32,
    "week_ending": pl.Date, "commitments_mt": pl.Float64,
    "last_season_mt": pl.Float64, "pace_projection_mt": pl.Float64,
    "forecast_mt": pl.Float64, "forecast_low_mt": pl.Float64,
    "forecast_high_mt": pl.Float64, "pace_weight": pl.Float64,
    "n_train_seasons": pl.Int64, "n_range_seasons": pl.Int64, "final_mt": pl.Float64,
}


def season_frame(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per crop, season and week, with both estimates and the outcome.

    ``final_mt`` is null for the season in progress. Rows without a pace
    estimate (the first seasons of the data) or without last season's total
    are left out.
    """
    return conn.execute("""
        WITH p AS (
            SELECT *, CAST(left(marketing_year, 4) AS INTEGER) AS season
            FROM grain_export_pace
        ),
        totals AS (
            SELECT commodity, season, any_value(final_shipped_mt) AS final_mt
            FROM p GROUP BY ALL
        ),
        t AS (
            SELECT *, lag(final_mt) OVER (PARTITION BY commodity ORDER BY season)
                AS last_season_mt
            FROM totals
        )
        SELECT p.commodity, p.marketing_year, p.season, p.my_week, p.week_ending,
            p.commitments_mt, t.last_season_mt, p.pace_projection_mt, t.final_mt
        FROM p JOIN t USING (commodity, season)
        WHERE p.pace_projection_mt > 0 AND t.last_season_mt > 0
        ORDER BY p.commodity, p.season, p.my_week
    """).pl()


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def fit_weight(train: pl.DataFrame) -> float | None:
    """Weight on the pace estimate, fit on finished seasons' rows (squared
    error in logs). None if there are too few seasons."""
    rows = train.filter(pl.col("final_mt").is_not_null())
    if rows["season"].n_unique() < MIN_TRAIN_SEASONS:
        return None
    # In logs relative to last season: outcome y, pace estimate x.
    y = (rows["final_mt"] / rows["last_season_mt"]).log().to_list()
    x = (rows["pace_projection_mt"] / rows["last_season_mt"]).log().to_list()
    return min(
        WEIGHT_GRID,
        key=lambda a: sum((yi - a * xi) ** 2 for yi, xi in zip(y, x, strict=True)),
    )


def _forecast_crop(crop: pl.DataFrame, commodity: str) -> list[dict[str, Any]]:
    """Every season of one crop, oldest first, so each season's range can use
    the misses of the forecasts made before it."""
    out: list[dict[str, Any]] = []
    # (season, week) -> log miss of that out-of-sample forecast
    misses: dict[tuple[int, int], float] = {}
    for season in sorted(crop["season"].unique().to_list()):
        history = crop.filter(pl.col("season") < season)
        n_train = history.filter(pl.col("final_mt").is_not_null())["season"].n_unique()
        weights: dict[int, float | None] = {}
        season_rows: list[dict[str, Any]] = []
        for row in crop.filter(pl.col("season") == season).iter_rows(named=True):
            week = row["my_week"]
            if week not in weights:
                if n_train < MIN_TRAIN_SEASONS:
                    weights[week] = None
                elif commodity in FIXED_WEIGHTS:
                    weights[week] = FIXED_WEIGHTS[commodity]
                else:
                    weights[week] = fit_weight(
                        history.filter((pl.col("my_week") - week).abs() <= WEEK_WINDOW)
                    )
            weight = weights[week]
            if weight is None:
                continue
            base = row["last_season_mt"]
            forecast = base * math.exp(weight * math.log(row["pace_projection_mt"] / base))

            nearby = {k: m for k, m in misses.items() if abs(k[1] - week) <= WEEK_WINDOW}
            n_range = len({s for s, _ in nearby})
            low = high = None
            if n_range >= MIN_RANGE_SEASONS:
                low = forecast * math.exp(_quantile(list(nearby.values()), RANGE_QUANTILES[0]))
                high = forecast * math.exp(_quantile(list(nearby.values()), RANGE_QUANTILES[1]))
            season_rows.append({
                "commodity": commodity,
                "marketing_year": row["marketing_year"],
                "my_week": week,
                "week_ending": row["week_ending"],
                "commitments_mt": row["commitments_mt"],
                "last_season_mt": base,
                "pace_projection_mt": row["pace_projection_mt"],
                "forecast_mt": forecast,
                "forecast_low_mt": low,
                "forecast_high_mt": high,
                "pace_weight": weight,
                "n_train_seasons": n_train,
                "n_range_seasons": n_range,
                "final_mt": row["final_mt"],
            })
        # Only once a season is over do its misses inform later seasons' ranges.
        for r in season_rows:
            if r["final_mt"]:
                misses[(season, r["my_week"])] = math.log(r["final_mt"] / r["forecast_mt"])
        out += season_rows
    return out


def forecast_seasons(frame: pl.DataFrame, seasons: list[int] | None = None) -> pl.DataFrame:
    """Forecast every week of every season, each using only seasons that had
    finished before it began. ``seasons`` limits what is returned."""
    rows: list[dict[str, Any]] = []
    for commodity in sorted(frame["commodity"].unique().to_list()):
        rows += _forecast_crop(frame.filter(pl.col("commodity") == commodity), commodity)
    out = pl.DataFrame(rows, schema=_SCHEMA)
    if seasons is not None:
        out = out.filter(
            pl.col("marketing_year").str.slice(0, 4).cast(pl.Int32).is_in(seasons)
        )
    return out


def create_grain_export_forecast(conn: duckdb.DuckDBPyConnection) -> int:
    """Rebuild ``grain_export_forecast``: every week of each crop's latest
    season, so the table shows how the forecast has moved. Returns its row count.
    """
    forecasts = forecast_seasons(season_frame(conn))
    if forecasts.height:
        latest = pl.col("marketing_year") == pl.col("marketing_year").max().over("commodity")
        forecasts = forecasts.filter(latest)
    conn.register("_grain_forecast", forecasts.to_arrow())
    try:
        conn.execute(f"CREATE OR REPLACE TABLE {TABLE} AS SELECT * FROM _grain_forecast")
    finally:
        conn.unregister("_grain_forecast")
    logger.info("Created %s with %d rows", TABLE, forecasts.height)
    return forecasts.height
