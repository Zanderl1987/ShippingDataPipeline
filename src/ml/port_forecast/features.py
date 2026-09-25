"""Model inputs for forecasting weekly port calls.

One row per origin x port x horizon, like the baselines (see ``baselines``
for origins and horizons). Every input uses only weeks up to the origin, or
the calendar, which is known in advance. Groups:

- Port history at the origin: recent weeks, averages over 4 to 52 weeks,
  volatility, trend, and change on a year earlier.
- Last year around the target week: its value, and how much traffic moved
  over the same stretch a year before.
- Calendar for the target week: week of year, and weeks to/from Chinese New
  Year and the two Eids, which move each year.
- Port traits from ``port_profiles``: continent, location, fleet mix.

The model predicts ``target``: log(1 + calls) minus log(1 + the 4-week
average), i.e. the change from the recent level, which is comparable across
big and small ports.
"""
from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache

import polars as pl

from src.ml.port_forecast.baselines import HORIZONS, YEAR, _trailing_mean

#: How far a moving holiday is tracked; further than this counts as "far".
HOLIDAY_WINDOW_WEEKS = 8
_HOLIDAYS = {
    "weeks_from_chinese_new_year": "Chinese New Year",
    "weeks_from_eid_al_fitr": "Eid al-Fitr",
    "weeks_from_eid_al_adha": "Eid al-Adha",
}
FLEET_TYPES = ["container", "dry_bulk", "general_cargo", "roro", "tanker"]

HISTORY = [
    "lag_0", "lag_1", "lag_2", "lag_3",
    "mean_4", "mean_8", "mean_13", "mean_52",
    "cv_13", "trend_4_vs_13", "change_vs_year_ago",
]
LAST_YEAR = ["last_year", "last_year_vs_its_mean_4", "seasonal_avg"]
CALENDAR = ["horizon", "target_week_of_year", *_HOLIDAYS]
PORT = ["continent", "latitude", "longitude", "vessel_count_total"] + [
    f"share_{t}" for t in FLEET_TYPES
]
FEATURES = HISTORY + LAST_YEAR + CALENDAR + PORT
CATEGORICAL = ["continent"]


def _ratio(num: pl.Expr, den: pl.Expr) -> pl.Expr:
    """num / den, null when den is 0 or null (a port with no calls then)."""
    return num / pl.when(den > 0).then(den)


@lru_cache
def _holiday_starts(name: str, first_year: int, last_year: int) -> tuple[date, ...]:
    """First day of each occurrence of a Singapore public holiday. Singapore
    observes all three, and ``holidays`` computes them for future years."""
    import holidays

    days = holidays.country_holidays(
        "SG", years=range(first_year, last_year + 1), language="en_US"
    )
    hits = sorted(d for d, n in days.items() if n.startswith(name) and "observed" not in n)
    # Consecutive days are one holiday; keep the first.
    return tuple(d for i, d in enumerate(hits) if i == 0 or (d - hits[i - 1]).days > 1)


def holiday_offsets(weeks: pl.Series) -> pl.DataFrame:
    """For each week start: weeks from the nearest occurrence of each moving
    holiday to the middle of that week (negative = the holiday is still ahead),
    clipped to +-HOLIDAY_WINDOW_WEEKS."""
    uniq: list[date] = weeks.unique().sort().to_list()
    out: dict[str, pl.Series] = {"week_start": pl.Series(uniq, dtype=pl.Date)}
    limit = HOLIDAY_WINDOW_WEEKS
    for col, name in _HOLIDAYS.items():
        starts = _holiday_starts(name, uniq[0].year - 1, uniq[-1].year + 1)
        vals: list[float] = []
        for w in uniq:
            mid = w + timedelta(days=3)
            nearest = min(((mid - s).days for s in starts), key=abs)
            vals.append(max(-limit, min(limit, round(nearest / 7, 1))))
        out[col] = pl.Series(vals, dtype=pl.Float32)
    return pl.DataFrame(out)


def port_traits(profiles: pl.DataFrame) -> pl.DataFrame:
    total = pl.col("vessel_count_total")
    return profiles.select(
        "port_id",
        "continent",
        "latitude",
        "longitude",
        "vessel_count_total",
        *[
            (pl.col(f"vessel_count_{t}") / pl.when(total > 0).then(total)).alias(f"share_{t}")
            for t in FLEET_TYPES
        ],
    )


def build_features(
    grid: pl.DataFrame,
    profiles: pl.DataFrame,
    origins: list[date] | None = None,
    horizons: tuple[int, ...] = HORIZONS,
    require_actual: bool = True,
) -> pl.DataFrame:
    """Keys (origin_week, target_week, port_id), ``actual``, ``target`` and the
    FEATURES columns. Rows with no recent history (no 4-week average) are
    dropped; so are rows with no actual when ``require_actual``."""
    y = pl.col("y")
    base = grid.with_columns(
        *[y.shift(k).over("port_id").alias(f"lag_{k}") for k in range(4)],
        *[_trailing_mean(y, n).alias(f"mean_{n}") for n in (4, 8, 13, 52)],
        _trailing_mean(y * y, 13).alias("_sq_13"),
        y.shift(YEAR).over("port_id").alias("_ly"),
        pl.mean_horizontal(*[y.shift(YEAR * k).over("port_id") for k in (1, 2, 3)]).alias(
            "_seasonal"
        ),
    ).with_columns(
        _ratio((pl.col("_sq_13") - pl.col("mean_13") ** 2).clip(0).sqrt(), pl.col("mean_13"))
        .alias("cv_13"),
        _ratio(pl.col("mean_4"), pl.col("mean_13")).alias("trend_4_vs_13"),
        _ratio(pl.col("mean_8"), pl.col("mean_8").shift(YEAR).over("port_id")).alias(
            "change_vs_year_ago"
        ),
        pl.col("mean_4").shift(YEAR).over("port_id").alias("_mean_4_year_ago"),
    )
    frames = []
    for h in horizons:
        ahead = {c: pl.col(c).shift(-h).over("port_id") for c in ("y", "_ly", "_seasonal")}
        frame = base.select(
            pl.col("week_start").alias("origin_week"),
            (pl.col("week_start") + pl.duration(weeks=h)).alias("target_week"),
            "port_id",
            ahead["y"].alias("actual"),
            *HISTORY,
            ahead["_ly"].alias("last_year"),
            _ratio(ahead["_ly"], pl.col("_mean_4_year_ago")).alias("last_year_vs_its_mean_4"),
            ahead["_seasonal"].alias("seasonal_avg"),
            pl.lit(h, dtype=pl.Int8).alias("horizon"),
        ).filter(pl.col("mean_4").is_not_null())
        if origins is not None:
            frame = frame.filter(pl.col("origin_week").is_in(origins))
        if require_actual:
            frame = frame.filter(pl.col("actual").is_not_null())
        frames.append(frame)
    out = pl.concat(frames)

    cal = holiday_offsets(out["target_week"]).rename({"week_start": "target_week"})
    return (
        out.join(cal, on="target_week", how="left")
        .with_columns(pl.col("target_week").dt.week().cast(pl.Int8).alias("target_week_of_year"))
        .join(port_traits(profiles), on="port_id", how="left")
        .with_columns(
            ((pl.col("actual") + 1).log() - (pl.col("mean_4") + 1).log()).alias("target"),
            pl.col("continent").cast(pl.Categorical),
        )
        .select("origin_week", "target_week", "port_id", "actual", "target", *FEATURES)
        .sort("origin_week", "horizon", "port_id")
    )
