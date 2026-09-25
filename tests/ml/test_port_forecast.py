from __future__ import annotations

from collections.abc import Callable
from datetime import date, timedelta

import polars as pl
import pytest

from src.ml.port_forecast.backtest import run
from src.ml.port_forecast.baselines import (
    BASELINES,
    backtest_origins,
    baseline_forecasts,
    weekly_grid,
)
from src.ml.port_forecast.evaluate import score

MONDAY = date(2020, 1, 6)


def _week(i: int) -> date:
    return MONDAY + timedelta(weeks=i)


def _weekly(
    n: int, calls: Callable[[int], float], port: str = "p1", partial: set[int] | None = None
) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "port_id": [port] * n,
            "week_start": [_week(i) for i in range(n)],
            "is_complete_week": [i not in (partial or set()) for i in range(n)],
            "portcalls": [int(calls(i)) for i in range(n)],
        }
    )


def _row(forecasts: pl.DataFrame, origin: int, h: int, port: str = "p1") -> dict:
    return forecasts.filter(
        (pl.col("origin_week") == _week(origin))
        & (pl.col("horizon") == h)
        & (pl.col("port_id") == port)
    ).row(0, named=True)


def test_grid_fills_gaps_and_blanks_partial_weeks() -> None:
    weekly = _weekly(5, lambda i: 10 + i, partial={4}).filter(
        pl.col("week_start") != _week(2)  # a week missing from the feed
    )
    grid = weekly_grid(weekly)
    assert grid["week_start"].to_list() == [_week(i) for i in range(5)]
    assert grid["y"].to_list() == [10.0, 11.0, None, 13.0, None]


def test_each_baseline_by_hand() -> None:
    # Week i has i calls, so every baseline has a known answer.
    grid = weekly_grid(_weekly(200, lambda i: i))
    row = _row(baseline_forecasts(grid), origin=160, h=2)
    assert row["target_week"] == _week(162) and row["actual"] == 162
    assert row["last_year"] == 110  # 162 - 52
    assert row["last_4"] == pytest.approx((157 + 158 + 159 + 160) / 4)
    assert row["seasonal_avg"] == pytest.approx((110 + 58 + 6) / 3)
    ratio = sum(range(153, 161)) / sum(range(101, 109))
    assert row["last_year_scaled"] == pytest.approx(110 * ratio)
    assert row["size_band"] == "large (50+/wk)"


def test_forecasts_never_see_past_the_origin() -> None:
    base = _weekly(200, lambda i: 20 + (i % 7))
    changed = _weekly(200, lambda i: 20 + (i % 7) if i <= 160 else 999)
    cols = ["origin_week", "horizon", *BASELINES]
    a = baseline_forecasts(weekly_grid(base)).filter(pl.col("origin_week") == _week(160))
    b = baseline_forecasts(weekly_grid(changed)).filter(pl.col("origin_week") == _week(160))
    assert a.select(cols).equals(b.select(cols))
    assert b["actual"].unique().to_list() == [999.0]


def test_scaling_follows_a_port_that_doubled() -> None:
    grid = weekly_grid(_weekly(120, lambda i: 10 if i < 60 else 20))
    row = _row(baseline_forecasts(grid), origin=100, h=1)
    assert row["last_year"] == 10 and row["last_year_scaled"] == pytest.approx(20)


def test_unknown_weeks_are_not_zeros() -> None:
    # The target week is partial: no row. A partial week in the history is skipped.
    grid = weekly_grid(_weekly(120, lambda i: 10, partial={100, 102}))
    fc = baseline_forecasts(grid)
    assert fc.filter(
        (pl.col("origin_week") == _week(101)) & (pl.col("horizon") == 1)
    ).is_empty()
    assert _row(fc, origin=101, h=2)["last_4"] == pytest.approx(10)


def test_origins_stop_before_the_data_runs_out() -> None:
    grid = weekly_grid(_weekly(100, lambda i: 5, partial={99}))
    origins = backtest_origins(grid, start=_week(80), every_weeks=4, max_horizon=4)
    assert origins == [_week(80), _week(84), _week(88), _week(92)]  # last complete: 98


def test_score_on_common_rows() -> None:
    fc = pl.DataFrame(
        {
            "horizon": [1, 1, 1],
            "actual": [10.0, 20.0, 30.0],
            "last_year": [12.0, 16.0, 30.0],  # abs errors 2, 4, 0
            "last_4": [10.0, 22.0, None],  # third row dropped for everyone
        }
    )
    (row,) = score(fc, ["last_year", "last_4"], reference="last_year").to_dicts()
    assert (row["n"], row["calls"]) == (2, 30.0)
    assert row["wape_last_year"] == pytest.approx(6 / 30)
    assert row["wape_last_4"] == pytest.approx(2 / 30)
    assert row["vs_ref_last_4"] == pytest.approx(2 / 6)
    assert row["vs_ref_last_year"] == 1.0
    # The reference joins the common-row filter even when not scored itself.
    (row,) = score(fc.drop("last_4"), ["last_year"], reference="last_year").to_dicts()
    assert row["n"] == 3


def test_run_end_to_end_two_ports() -> None:
    weekly = pl.concat(
        [_weekly(200, lambda i: 30 + i % 5, "p1"), _weekly(200, lambda i: 2, "p2")]
    )
    forecasts, scores = run(weekly, start=_week(160))
    assert set(forecasts["port_id"]) == {"p1", "p2"}
    assert set(forecasts["size_band"]) == {"medium (5-50/wk)", "small (<5/wk)"}
    assert scores["by horizon"]["horizon"].to_list() == [1, 2, 3, 4]
