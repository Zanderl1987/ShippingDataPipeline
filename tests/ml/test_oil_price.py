from __future__ import annotations

import json
import math
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from src.ml.oil_price.features import (
    build_features,
    feature_sets,
    flow_features,
    fridays,
    price_features,
    targets,
    weekly_wide,
)
from src.ml.oil_price.train import (
    MODELS,
    Progress,
    Ridge,
    TrainConfig,
    clark_west,
    score,
    train_and_backtest,
)

STOCK_SERIES = ["stock_crude", "stock_cushing", "stock_gasoline", "stock_distillate",
                "stock_spr"]


def _daily_prices(start: date, days: int, rate: float = 0.001) -> pl.DataFrame:
    d = [start + timedelta(days=i) for i in range(days)]
    brent = [100 * math.exp(rate * i) for i in range(days)]
    return pl.DataFrame({"price_date": d, "brent_usd": brent,
                         "wti_usd": [b - 5 for b in brent]})


def test_fridays_start_on_the_first_friday() -> None:
    out = fridays(date(2026, 9, 21), date(2026, 10, 9))  # a Monday
    assert out == [date(2026, 9, 25), date(2026, 10, 2), date(2026, 10, 9)]


def test_price_features_and_targets_use_log_changes() -> None:
    prices = _daily_prices(date(2020, 1, 1), 800)
    origin = [date(2021, 1, 1)]
    f = price_features(prices, origin).row(0, named=True)
    assert f["r1"] == pytest.approx(0.007) and f["r4"] == pytest.approx(0.028)
    assert f["spread"] == pytest.approx(5.0) and f["dd52"] == pytest.approx(0.0)
    t = targets(prices, origin).row(0, named=True)
    assert t["y1"] == pytest.approx(0.007) and t["y4"] == pytest.approx(0.028)


def test_targets_are_null_until_the_day_arrives() -> None:
    prices = _daily_prices(date(2021, 1, 1), 10)  # through 2021-01-10
    t = targets(prices, [date(2021, 1, 1)]).row(0, named=True)
    assert t["y1"] is not None and t["y2"] is None and t["y4"] is None


def test_weekly_wide_blanks_part_weeks_and_flow_ratios() -> None:
    weeks = fridays(date(2021, 1, 1), date(2021, 3, 5))
    long = pl.DataFrame({"week_end": weeks, "series": "exp_world",
                         "value": [100.0] * (len(weeks) - 1) + [200.0],
                         "days": [7] * (len(weeks) - 2) + [3, 7]})
    wide = weekly_wide(long)
    assert wide["exp_world"][-2] is None  # 3 days reported
    f = flow_features(wide)
    assert f["exp_world_w4"][-1] == pytest.approx(math.log(2.0))


def _synthetic(seed: int = 0) -> tuple[pl.DataFrame, list[date]]:
    """Brent's next-week move follows a tanker-export signal published a week
    earlier; stocks and chokepoints carry nothing."""
    rng = np.random.default_rng(seed)
    weeks = fridays(date(2018, 1, 5), date(2022, 6, 24))
    signal = rng.normal(size=len(weeks))
    exports = 100 * np.exp(0.3 * signal)
    # Weekly Brent: the move from Friday F to F+7 depends on the week ending F-7.
    price = [100.0]
    for k in range(1, len(weeks)):
        s = signal[k - 2] if k >= 2 else 0.0
        price.append(price[-1] * math.exp(0.02 * s + rng.normal(0, 0.01)))
    days = [weeks[0] + timedelta(days=i) for i in range((weeks[-1] - weeks[0]).days + 1)]
    daily = pl.DataFrame({"price_date": days}).join_asof(
        pl.DataFrame({"price_date": weeks, "brent_usd": price}), on="price_date",
        strategy="backward").with_columns((pl.col("brent_usd") - 5).alias("wti_usd"))
    flows = pl.DataFrame({"week_end": weeks, "series": "exp_world", "value": exports, "days": 7})
    chokes = pl.DataFrame({"week_end": weeks, "series": "choke_hormuz", "value": 50.0,
                           "days": 7})
    stocks = pl.concat([pl.DataFrame({"week_end": weeks, "series": s,
                                      "value": 1000.0 + rng.normal(0, 1, len(weeks))})
                        for s in STOCK_SERIES])
    fx = build_features(daily, flows, chokes, stocks, weeks)
    return fx, weeks


def test_build_features_uses_the_week_before_the_origin() -> None:
    fx, _ = _synthetic()
    row = fx.filter(pl.col("origin") == date(2020, 6, 5)).row(0, named=True)
    assert row["week_end"] == date(2020, 5, 29)
    sets = feature_sets(fx)
    assert sets["full"][: len(sets["price_stocks"])] == sets["price_stocks"]
    assert "exp_world_w4" in sets["full"] and "exp_world_w4" not in sets["price_stocks"]


def test_ridge_recovers_a_linear_signal() -> None:
    rng = np.random.default_rng(1)
    x = rng.normal(size=(500, 3))
    y = 0.5 * x[:, 0] - 0.2 * x[:, 2] + 0.01
    x[5, 1] = np.nan  # a missing input counts as average
    m = Ridge(alpha=1e-6).fit(x, y)
    assert m.coef[0] / m.scale[0] == pytest.approx(0.5, rel=1e-3)
    assert m.predict(x[:2]) == pytest.approx(y[:2], abs=1e-3)


def test_clark_west_separates_signal_from_noise() -> None:
    rng = np.random.default_rng(2)
    y = rng.normal(0, 1, 400)
    assert clark_west(y, 0.5 * y + rng.normal(0, 0.1, 400), 0)[1] < 0.001
    assert clark_west(y, rng.normal(0, 0.5, 400), 0)[1] > 0.5


def test_backtest_finds_the_flow_signal(tmp_path) -> None:  # noqa: ANN001
    pytest.importorskip("lightgbm")
    fx, _ = _synthetic()
    config = TrainConfig(first_origin=date(2019, 3, 1), eval_start=date(2021, 1, 1),
                         retrain_weeks=8, valid_weeks=26, num_boost_round=60,
                         early_stopping_rounds=20)
    progress = Progress(tmp_path, config)
    out = train_and_backtest(fx, config, progress)
    assert set(MODELS) <= set(out.columns)
    assert (out["origin"] >= out["cutoff"]).all()  # never predicts before its cutoff
    h1 = {r["model"]: r for r in score(out) if r["h"] == 1}
    assert h1["ridge_full"]["r2"] > 0.3 > h1["ridge_price_stocks"]["r2"]
    assert h1["ridge_full"]["cw_p"] < 0.01 and h1["ridge_full"]["hit"] > 0.7
    state = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert len(state["folds"]) == progress.state["n_folds"]
    assert len(state["predictions"]) == out.height
    top = state["importance"]["1"][0]["feature"]
    assert top.startswith("exp_world")
