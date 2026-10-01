from __future__ import annotations

from datetime import date, timedelta

import duckdb
import polars as pl
import pytest

from src.ml.grain_forecast import model
from src.ml.grain_forecast.model import fit_weight, forecast_seasons

FIRST, LAST = 2000, 2015  # 16 seasons; the last one unfinished
WEEKS = range(1, 6)


def _frame(commodity: str = "Soybeans", pace_error: float = 0.0) -> pl.DataFrame:
    """Each season ships 100 + 5 * (season - FIRST) tons. The pace estimate
    misses the final total by ``pace_error`` (alternating sign)."""
    rows = []
    for season in range(FIRST, LAST + 1):
        final = 100.0 + 5 * (season - FIRST)
        sign = 1 if season % 2 else -1
        for week in WEEKS:
            rows.append({
                "commodity": commodity,
                "marketing_year": f"{season}/{season + 1}",
                "season": season,
                "my_week": week,
                "week_ending": date(season, 9, 1) + timedelta(weeks=week),
                "commitments_mt": final / 2,
                "last_season_mt": final - 5,
                "pace_projection_mt": final * (1 + sign * pace_error),
                "final_mt": None if season == LAST else final,
            })
    return pl.DataFrame(rows).with_columns(pl.col("my_week").cast(pl.Int32))


def test_weight_is_one_when_pace_is_exact() -> None:
    assert fit_weight(_frame()) == 1.0


def test_weight_leans_on_last_season_when_pace_is_noisy() -> None:
    assert fit_weight(_frame(pace_error=0.5)) < 0.5


def test_no_weight_without_enough_seasons() -> None:
    few = _frame().filter(pl.col("season") < FIRST + model.MIN_TRAIN_SEASONS - 1)
    assert fit_weight(few) is None


def test_forecasts_start_after_enough_seasons_and_never_look_ahead() -> None:
    frame = _frame(pace_error=0.2)
    out = forecast_seasons(frame)
    first = out["marketing_year"].str.slice(0, 4).cast(pl.Int32).min()
    assert first == FIRST + model.MIN_TRAIN_SEASONS

    # Changing a later season's outcome leaves earlier forecasts untouched.
    cut = 2010
    changed = frame.with_columns(
        pl.when(pl.col("season") == cut + 2).then(pl.col("final_mt") * 3)
        .otherwise(pl.col("final_mt")).alias("final_mt")
    )
    def through(df: pl.DataFrame) -> pl.DataFrame:
        return df.filter(pl.col("marketing_year").str.slice(0, 4).cast(pl.Int32) <= cut + 2)
    cols = ["forecast_mt", "forecast_low_mt", "forecast_high_mt"]
    assert through(forecast_seasons(changed)).select(cols).equals(through(out).select(cols))


def test_range_needs_earlier_out_of_sample_seasons() -> None:
    out = forecast_seasons(_frame(pace_error=0.2))
    with_range = out.filter(pl.col("forecast_low_mt").is_not_null())
    first_ranged = with_range["marketing_year"].str.slice(0, 4).cast(pl.Int32).min()
    assert first_ranged == FIRST + model.MIN_TRAIN_SEASONS + model.MIN_RANGE_SEASONS
    assert (with_range["forecast_low_mt"] <= with_range["forecast_high_mt"]).all()
    # Every season here grows, so past forecasts all ran low and the range sits
    # above the forecast: it reports the misses as they were.
    assert (with_range["forecast_low_mt"] > with_range["forecast_mt"]).all()


def test_corn_uses_the_pace_estimate_alone() -> None:
    out = forecast_seasons(_frame("Corn", pace_error=0.5))
    assert set(out["pace_weight"].to_list()) == {1.0}
    assert out["forecast_mt"].to_list() == pytest.approx(out["pace_projection_mt"].to_list())


def test_create_table_keeps_latest_season_only() -> None:
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE grain_export_pace (commodity VARCHAR, marketing_year VARCHAR,
            my_week INTEGER, week_ending DATE, commitments_mt DOUBLE,
            pace_projection_mt DOUBLE, final_shipped_mt DOUBLE)
    """)
    frame = _frame(pace_error=0.1)
    conn.register("f", frame.to_arrow())
    conn.execute("""
        INSERT INTO grain_export_pace SELECT commodity, marketing_year, my_week,
            week_ending, commitments_mt, pace_projection_mt, final_mt FROM f
    """)
    assert model.create_grain_export_forecast(conn) == len(WEEKS)
    rows = conn.execute(
        "SELECT DISTINCT marketing_year, final_mt IS NULL FROM grain_export_forecast"
    ).fetchall()
    assert rows == [(f"{LAST}/{LAST + 1}", True)]
