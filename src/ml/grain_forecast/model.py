"""Forecast a marketing year's total US exports from how far booking has got.

Three estimates are available at a given week of a season:

- **USDA**: the US export projection in USDA's latest monthly WASDE report
  (``usda_wasde``), as published before that week's sales data came out.
  USDA weighs crop size, prices and competing exporters, so early in the
  season it is the best single estimate.
- **pace**: tons committed so far divided by the share of a season's exports
  that has usually been committed by this week (``grain_export_pace``). It
  uses every week's sales, while USDA's figure can be a month old.
- **last season**: the total shipped last season.

The forecast starts from an anchor (USDA's projection, or last season's total
for seasons before WASDE data begins in 2010) and moves it toward pace:

    log(forecast) = log(anchor) + level + a * log(pace / anchor)

``a`` (0 to 1) is how far to trust pace, and ``level`` corrects for USDA
counting exports slightly differently from the export sales program (USDA's
season totals run 2-4% above the sales program's). Both are fit separately
for each crop, anchor and week of the season, on that week and the two
either side, in seasons that had finished before the one being forecast.
Richer models were tried and did worse out of sample (README.md).

Corn's weight is fixed (``FIXED_WEIGHTS``): USDA's projection alone, with
the level correction; without USDA, pace alone. Fitted weights for corn did
worse in both cases. Corn's season totals swing far more than soybeans'
(+110% after the 2012 drought, -62% the next season), and weights fit to
those swings did not carry over to ordinary years.

The likely range is built from the forecast's own misses on earlier seasons
with the same anchor, at the same week of the season give or take
``WEEK_WINDOW``, each made before that season's outcome was known. With
fewer than ``MIN_RANGE_SEASONS`` such seasons there is no range. The range
is not centred on the forecast: if past forecasts at this week mostly ran
low, it sits mostly above.

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
#: Finished seasons with the same anchor needed before a forecast is made.
MIN_TRAIN_SEASONS = {"usda": 5, "last_season": 8}
#: Earlier out-of-sample forecasts needed before a range is given.
MIN_RANGE_SEASONS = 5
WEIGHT_GRID = [i / 20 for i in range(21)]
RANGE_QUANTILES = (0.1, 0.9)
#: (crop, anchor) pairs whose pace weight is fixed instead of fit.
FIXED_WEIGHTS = {("Corn", "usda"): 0.0, ("Corn", "last_season"): 1.0}
#: Anchors whose level is fit; last season's total is used as it stands.
FIT_LEVEL = {"usda": True, "last_season": False}
#: First week of the season from which the forecast has beaten simply
#: repeating last season's total in the backtest (README.md).
RELIABLE_FROM_WEEK = {"Corn": 1, "Soybeans": 1}

#: WASDE rows holding the US export projection, in million metric tons.
_WASDE_SQL = """
    SELECT CASE commodity WHEN 'Corn' THEN 'Corn' ELSE 'Soybeans' END AS commodity,
        CAST(left(market_year, 4) AS INTEGER) AS season,
        release_date, value * 1e6 AS usda_mt
    FROM usda_wasde
    WHERE region = 'United States' AND attribute = 'Exports' AND period = 'Annual'
      AND unit = 'Million Metric Tons' AND reliability_projection IS NULL
      AND report_title IN ('World Corn Supply and Use', 'World Soybean Supply and Use')
      AND commodity IN ('Corn', 'Oilseed, Soybean')
      AND value IS NOT NULL
"""

_SCHEMA = {
    "commodity": pl.Utf8, "marketing_year": pl.Utf8, "my_week": pl.Int32,
    "week_ending": pl.Date, "commitments_mt": pl.Float64,
    "last_season_mt": pl.Float64, "pace_projection_mt": pl.Float64,
    "usda_mt": pl.Float64, "usda_release_date": pl.Date, "anchor": pl.Utf8,
    "forecast_mt": pl.Float64, "forecast_low_mt": pl.Float64,
    "forecast_high_mt": pl.Float64, "pace_weight": pl.Float64,
    "level_adjust": pl.Float64, "n_train_seasons": pl.Int64,
    "n_range_seasons": pl.Int64, "final_mt": pl.Float64,
}


def _has_table(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def season_frame(conn: duckdb.DuckDBPyConnection) -> pl.DataFrame:
    """One row per crop, season and week, with the estimates and the outcome.

    ``final_mt`` is null for the season in progress. ``usda_mt`` is USDA's
    projection from the latest WASDE released by the day the week's sales
    were published (the Thursday after the week ended), or null. Rows
    without a pace estimate or without last season's total are left out.
    """
    frame = conn.execute("""
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
            CAST(p.week_ending + INTERVAL 7 DAY AS DATE) AS published,
            p.commitments_mt, t.last_season_mt, p.pace_projection_mt, t.final_mt
        FROM p JOIN t USING (commodity, season)
        WHERE p.pace_projection_mt > 0 AND t.last_season_mt > 0
    """).pl()
    if _has_table(conn, "usda_wasde"):
        wasde = conn.execute(_WASDE_SQL).pl().rename({"release_date": "usda_release_date"})
    else:
        wasde = pl.DataFrame(schema={
            "commodity": pl.Utf8, "season": pl.Int32,
            "usda_release_date": pl.Date, "usda_mt": pl.Float64,
        })
    frame = frame.sort("published").join_asof(
        wasde.with_columns(pl.col("season").cast(frame.schema["season"]))
        .sort("usda_release_date"),
        left_on="published", right_on="usda_release_date",
        by=["commodity", "season"], strategy="backward", check_sortedness=False,
    )
    return frame.drop("published").sort("commodity", "season", "my_week")


def _quantile(values: list[float], q: float) -> float:
    s = sorted(values)
    pos = q * (len(s) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def fit(
    y: list[float], x: list[float], *, weight: float | None, fit_level: bool,
) -> tuple[float, float]:
    """(pace weight, level) minimizing squared error of ``y ~ level + a * x``,
    where y and x are the outcome and pace in logs relative to the anchor.
    A given ``weight`` is kept; the level is 0 unless ``fit_level``."""
    n = len(y)

    def level_for(a: float) -> float:
        return sum(yi - a * xi for yi, xi in zip(y, x, strict=True)) / n if fit_level else 0.0

    def sse(a: float) -> float:
        b = level_for(a)
        return sum((yi - b - a * xi) ** 2 for yi, xi in zip(y, x, strict=True))

    a = weight if weight is not None else min(WEIGHT_GRID, key=sse)
    return a, level_for(a)


def _anchor(row: dict[str, Any]) -> tuple[str, float]:
    if row["usda_mt"] is not None and row["usda_mt"] > 0:
        return "usda", row["usda_mt"]
    return "last_season", row["last_season_mt"]


def _forecast_crop(
    crop: pl.DataFrame, commodity: str, fixed_weights: dict[tuple[str, str], float],
) -> list[dict[str, Any]]:
    """Every season of one crop, oldest first, so each season's range can use
    the misses of the forecasts made before it."""
    rows = crop.to_dicts()
    for r in rows:
        r["anchor"], r["anchor_mt"] = _anchor(r)
    out: list[dict[str, Any]] = []
    # (anchor, season, week) -> log miss of that out-of-sample forecast
    misses: dict[tuple[str, int, int], float] = {}
    for season in sorted({r["season"] for r in rows}):
        history = [r for r in rows if r["season"] < season and r["final_mt"]]
        season_rows: list[dict[str, Any]] = []
        fits: dict[tuple[str, int], tuple[float, float, int] | None] = {}
        for row in (r for r in rows if r["season"] == season):
            anchor, week = row["anchor"], row["my_week"]
            if (anchor, week) not in fits:
                train = [h for h in history
                         if h["anchor"] == anchor and abs(h["my_week"] - week) <= WEEK_WINDOW]
                n_train = len({h["season"] for h in train})
                if n_train < MIN_TRAIN_SEASONS[anchor]:
                    fits[(anchor, week)] = None
                else:
                    y = [math.log(h["final_mt"] / h["anchor_mt"]) for h in train]
                    x = [math.log(h["pace_projection_mt"] / h["anchor_mt"]) for h in train]
                    a, b = fit(y, x, weight=fixed_weights.get((commodity, anchor)),
                               fit_level=FIT_LEVEL[anchor])
                    fits[(anchor, week)] = (a, b, n_train)
            fitted = fits[(anchor, week)]
            if fitted is None:
                continue
            a, b, n_train = fitted
            centre = b + a * math.log(row["pace_projection_mt"] / row["anchor_mt"])
            forecast = row["anchor_mt"] * math.exp(centre)

            nearby = [m for (an, _, w), m in misses.items()
                      if an == anchor and abs(w - week) <= WEEK_WINDOW]
            n_range = len({s for (an, s, w) in misses
                           if an == anchor and abs(w - week) <= WEEK_WINDOW})
            low = high = None
            if n_range >= MIN_RANGE_SEASONS:
                low = forecast * math.exp(_quantile(nearby, RANGE_QUANTILES[0]))
                high = forecast * math.exp(_quantile(nearby, RANGE_QUANTILES[1]))
            season_rows.append({
                **{k: row[k] for k in (
                    "commodity", "marketing_year", "my_week", "week_ending",
                    "commitments_mt", "last_season_mt", "pace_projection_mt",
                    "usda_mt", "usda_release_date", "anchor", "final_mt",
                )},
                "forecast_mt": forecast,
                "forecast_low_mt": low,
                "forecast_high_mt": high,
                "pace_weight": a,
                "level_adjust": b,
                "n_train_seasons": n_train,
                "n_range_seasons": n_range,
            })
        # Only once a season is over do its misses inform later seasons' ranges.
        for r in season_rows:
            if r["final_mt"]:
                key = (r["anchor"], season, r["my_week"])
                misses[key] = math.log(r["final_mt"] / r["forecast_mt"])
        out += season_rows
    return out


def forecast_seasons(
    frame: pl.DataFrame,
    seasons: list[int] | None = None,
    fixed_weights: dict[tuple[str, str], float] | None = None,
) -> pl.DataFrame:
    """Forecast every week of every season, each using only seasons that had
    finished before it began. ``seasons`` limits what is returned;
    ``fixed_weights`` replaces ``FIXED_WEIGHTS`` (the backtest uses it to score
    USDA's projection alone)."""
    weights = FIXED_WEIGHTS if fixed_weights is None else fixed_weights
    rows: list[dict[str, Any]] = []
    for commodity in sorted(frame["commodity"].unique().to_list()):
        crop = frame.filter(pl.col("commodity") == commodity)
        rows += _forecast_crop(crop, commodity, weights)
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
