from __future__ import annotations

from datetime import date, timedelta

import duckdb
import polars as pl
import pytest

from src.ml.grain_forecast import model
from src.ml.grain_forecast.model import fit, forecast_seasons, season_frame

FIRST, LAST = 2000, 2015  # 16 seasons; the last one unfinished
WEEKS = range(1, 6)


def _frame(
    commodity: str = "Soybeans", pace_error: float = 0.0, usda_from: int | None = None,
) -> pl.DataFrame:
    """Each season ships 100 + 5 * (season - FIRST) tons. The pace estimate
    misses the final total by ``pace_error`` (alternating sign). From season
    ``usda_from`` on, USDA projects 3% above the final total."""
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
                "usda_mt": final * 1.03 if usda_from and season >= usda_from else None,
                "usda_release_date": None,
            })
    return pl.DataFrame(rows, schema_overrides={
        "usda_mt": pl.Float64, "usda_release_date": pl.Date,
    }).with_columns(pl.col("my_week").cast(pl.Int32))


def test_fit_weight_and_level() -> None:
    # Outcome = anchor exactly, pace noisy -> trust the anchor, no level shift.
    assert fit([0.0, 0.0, 0.0], [0.3, -0.3, 0.2], weight=None, fit_level=True) == (0.0, 0.0)
    # Outcome = pace exactly -> trust pace.
    a, b = fit([0.1, -0.2, 0.05], [0.1, -0.2, 0.05], weight=None, fit_level=False)
    assert (a, b) == (1.0, 0.0)
    # A fixed weight is kept; the level absorbs a constant gap.
    a, b = fit([-0.03, -0.03], [0.5, -0.5], weight=0.0, fit_level=True)
    assert a == 0.0 and b == pytest.approx(-0.03)


def test_forecasts_start_after_enough_seasons_and_never_look_ahead() -> None:
    frame = _frame(pace_error=0.2)
    out = forecast_seasons(frame)
    first = out["marketing_year"].str.slice(0, 4).cast(pl.Int32).min()
    assert first == FIRST + model.MIN_TRAIN_SEASONS["last_season"]

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
    assert first_ranged == (
        FIRST + model.MIN_TRAIN_SEASONS["last_season"] + model.MIN_RANGE_SEASONS
    )
    assert (with_range["forecast_low_mt"] <= with_range["forecast_high_mt"]).all()
    # Every season here grows, so past forecasts all ran low and the range sits
    # above the forecast: it reports the misses as they were.
    assert (with_range["forecast_low_mt"] > with_range["forecast_mt"]).all()


def test_corn_without_usda_uses_the_pace_estimate_alone() -> None:
    out = forecast_seasons(_frame("Corn", pace_error=0.5))
    assert set(out["anchor"].to_list()) == {"last_season"}
    assert set(out["pace_weight"].to_list()) == {1.0}
    assert out["forecast_mt"].to_list() == pytest.approx(out["pace_projection_mt"].to_list())


def test_usda_anchor_learns_the_level_gap() -> None:
    usda_from = 2004
    out = forecast_seasons(_frame("Corn", pace_error=0.5, usda_from=usda_from))
    usda = out.filter(pl.col("anchor") == "usda")
    first = usda["marketing_year"].str.slice(0, 4).cast(pl.Int32).min()
    assert first == usda_from + model.MIN_TRAIN_SEASONS["usda"]
    # Corn: USDA alone, corrected for USDA running 3% above the final total.
    assert set(usda["pace_weight"].to_list()) == {0.0}
    done = usda.filter(pl.col("final_mt").is_not_null())
    assert done["forecast_mt"].to_list() == pytest.approx(done["final_mt"].to_list())


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
    assert model.create_grain_export_forecast(conn) == len(WEEKS)  # no usda_wasde: fine
    rows = conn.execute(
        "SELECT DISTINCT marketing_year, final_mt IS NULL FROM grain_export_forecast"
    ).fetchall()
    assert rows == [(f"{LAST}/{LAST + 1}", True)]


def test_season_frame_uses_the_wasde_published_before_the_sales_data() -> None:
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE grain_export_pace AS SELECT * FROM (VALUES
            ('Soybeans', '2025/2026', 1, DATE '2025-09-04', 10.0, 40.0, 50.0),
            ('Soybeans', '2025/2026', 2, DATE '2025-09-11', 12.0, 41.0, 50.0),
            ('Soybeans', '2024/2025', 1, DATE '2024-09-05', 10.0, 40.0, 45.0)
        ) t(commodity, marketing_year, my_week, week_ending, commitments_mt,
            pace_projection_mt, final_shipped_mt)
    """)
    conn.execute("""
        CREATE TABLE usda_wasde AS SELECT * FROM (VALUES
            (DATE '2025-08-12', 'World Soybean Supply and Use', 'Exports', NULL,
             'Oilseed, Soybean', 'United States', '2025/26', 'Annual', 47.0,
             'Million Metric Tons'),
            (DATE '2025-09-12', 'World Soybean Supply and Use', 'Exports', NULL,
             'Oilseed, Soybean', 'United States', '2025/26', 'Annual', 46.0,
             'Million Metric Tons'),
            (DATE '2025-09-12', 'World Soybean Supply and Use', 'Exports', NULL,
             'Oilseed, Soybean', 'Brazil', '2025/26', 'Annual', 99.0,
             'Million Metric Tons')
        ) t(release_date, report_title, attribute, reliability_projection, commodity,
            region, market_year, period, value, unit)
    """)
    frame = season_frame(conn).filter(pl.col("marketing_year") == "2025/2026")
    # Week 1 sales came out Sep 11, before the Sep 12 WASDE; week 2 after it.
    assert frame["usda_mt"].to_list() == [47e6, 46e6]
    assert frame["usda_release_date"].to_list() == [date(2025, 8, 12), date(2025, 9, 12)]


def test_season_frame_reads_each_crops_wasde_line() -> None:
    conn = duckdb.connect()
    conn.execute("""
        CREATE TABLE grain_export_pace AS SELECT * FROM (VALUES
            ('Wheat', '2025/2026', 14, DATE '2025-09-04', 10.0, 20.0, NULL),
            ('Wheat', '2024/2025', 14, DATE '2024-09-05', 10.0, 20.0, 21.0),
            ('Corn', '2025/2026', 1, DATE '2025-09-04', 10.0, 70.0, NULL),
            ('Corn', '2024/2025', 1, DATE '2024-09-05', 10.0, 70.0, 60.0)
        ) t(commodity, marketing_year, my_week, week_ending, commitments_mt,
            pace_projection_mt, final_shipped_mt)
    """)
    conn.execute("""
        CREATE TABLE usda_wasde AS SELECT * FROM (VALUES
            (DATE '2025-08-12', 'World Wheat Supply and Use', 'Exports', NULL,
             'Wheat', 'United States', '2025/26', 'Annual', 23.8, 'Million Metric Tons'),
            (DATE '2025-08-12', 'World Corn Supply and Use', 'Exports', NULL,
             'Corn', 'United States', '2025/26', 'Annual', 72.4, 'Million Metric Tons'),
            -- The US table in bushels is not used.
            (DATE '2025-08-12', 'U.S. Wheat Supply and Use', 'Exports', NULL,
             'Wheat', 'United States', '2025/26', 'Annual', 875.0, 'Million Bushels')
        ) t(release_date, report_title, attribute, reliability_projection, commodity,
            region, market_year, period, value, unit)
    """)
    frame = season_frame(conn).filter(pl.col("marketing_year") == "2025/2026")
    got = dict(zip(frame["commodity"], frame["usda_mt"], strict=True))
    assert got == {"Corn": pytest.approx(72.4e6), "Wheat": pytest.approx(23.8e6)}
