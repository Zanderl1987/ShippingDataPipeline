from __future__ import annotations

import json
import math
from datetime import date, timedelta

import polars as pl
import pytest

from src.ml.port_forecast.baselines import weekly_grid
from src.ml.port_forecast.features import FEATURES, build_features, holiday_offsets
from tests.ml.test_port_forecast import MONDAY, _week, _weekly

pytest.importorskip("lightgbm")


def _profiles(*ports: str) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "port_id": list(ports),
            "continent": ["Europe"] * len(ports),
            "latitude": [51.9] * len(ports),
            "longitude": [4.2] * len(ports),
            "vessel_count_total": [100] * len(ports),
            **{f"vessel_count_{t}": [20] * len(ports) for t in
               ("container", "dry_bulk", "general_cargo", "roro", "tanker")},
        }
    )


def _row(fx: pl.DataFrame, origin: int, h: int) -> dict:
    return fx.filter(
        (pl.col("origin_week") == _week(origin)) & (pl.col("horizon") == h)
    ).row(0, named=True)


def test_features_by_hand() -> None:
    grid = weekly_grid(_weekly(200, lambda i: i + 1))
    row = _row(build_features(grid, _profiles("p1")), origin=160, h=2)
    assert row["actual"] == 163 and row["lag_0"] == 161 and row["lag_3"] == 158
    assert row["mean_4"] == pytest.approx((158 + 159 + 160 + 161) / 4)
    assert row["target"] == pytest.approx(math.log(164) - math.log(row["mean_4"] + 1))
    assert row["last_year"] == 111  # week 110 has 111 calls
    # Last year: target week vs the 4-week average at last year's origin.
    assert row["last_year_vs_its_mean_4"] == pytest.approx(111 / ((106 + 107 + 108 + 109) / 4))
    assert row["target_week_of_year"] == _week(162).isocalendar().week
    assert row["share_tanker"] == pytest.approx(0.2) and row["continent"] == "Europe"
    assert list(build_features(grid, _profiles("p1")).columns[5:]) == FEATURES


def test_features_never_see_past_the_origin() -> None:
    a = _weekly(200, lambda i: 20 + (i % 7))
    b = _weekly(200, lambda i: 20 + (i % 7) if i <= 160 else 999)
    cols = [c for c in FEATURES if c != "continent"]
    fa, fb = (
        build_features(weekly_grid(w), _profiles("p1"), [_week(160)]).select(cols)
        for w in (a, b)
    )
    assert fa.equals(fb)


def test_ratios_are_null_not_infinite_after_a_silent_spell() -> None:
    grid = weekly_grid(_weekly(200, lambda i: 0 if i < 120 else 5))
    fx = build_features(grid, _profiles("p1"))
    for col in ("change_vs_year_ago", "last_year_vs_its_mean_4", "cv_13", "trend_4_vs_13"):
        assert fx[col].drop_nulls().is_finite().all(), col
    assert _row(fx, origin=160, h=1)["change_vs_year_ago"] is None


def test_holiday_offsets() -> None:
    # Chinese New Year 2025 was 29 January; the week of 27 Jan has its middle on the 30th.
    cny = date(2025, 1, 27)
    out = holiday_offsets(pl.Series([cny, cny - timedelta(weeks=2), date(2025, 7, 7)]))
    got = dict(zip(out["week_start"], out["weeks_from_chinese_new_year"], strict=True))
    assert got[cny] == pytest.approx(0.1)
    assert got[cny - timedelta(weeks=2)] == pytest.approx(-1.9)
    assert got[date(2025, 7, 7)] == 8.0  # clipped


def test_train_and_backtest_end_to_end(tmp_path) -> None:
    from src.ml.port_forecast.train import MODEL, Progress, TrainConfig, train_and_backtest

    # Two ports with a yearly wave and a slow trend: learnable, not trivial.
    def wave(i: int, size: float) -> float:
        return size * (1 + 0.3 * math.sin(2 * math.pi * i / 52) + 0.002 * i)

    weekly = pl.concat(
        [_weekly(260, lambda i: wave(i, 40), "p1"), _weekly(260, lambda i: wave(i, 8), "p2")]
    )
    config = TrainConfig(
        eval_start=MONDAY + timedelta(weeks=200),
        train_start=MONDAY + timedelta(weeks=60),
        num_boost_round=30,
        early_stopping_rounds=10,
    )
    config.params |= {"min_data_in_leaf": 5, "num_leaves": 7}
    progress = Progress(tmp_path, config)
    result = train_and_backtest(weekly, _profiles("p1", "p2"), config, progress)

    assert result[MODEL].null_count() == 0 and (result[MODEL] >= 0).all()
    state = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert state["status"] == "done"
    assert len(state["folds"]) == state["n_folds"] >= 2
    assert state["folds"][0]["curve"]["valid"]
    assert {r["horizon"] for r in state["scores"]["by_horizon"]} == {1, 2, 3, 4}
    assert sum(r["share"] for r in state["importance"]) == pytest.approx(1, abs=0.01)
    # Every scored forecast is for an origin at or after its fold's cutoff.
    cutoffs = [date.fromisoformat(f["cutoff"]) for f in state["folds"]]
    assert result["origin_week"].min() == cutoffs[0]
