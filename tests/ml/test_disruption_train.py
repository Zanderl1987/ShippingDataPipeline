from __future__ import annotations

import json
from datetime import datetime

import polars as pl
import pytest

from src.ml.disruption_warning.baselines import RULES
from src.ml.disruption_warning.features import build_features
from src.ml.disruption_warning.train import (
    MODEL,
    TrainConfig,
    calibration,
    goal_check,
    pr_curve,
    train_and_backtest,
)
from src.ml.port_forecast.train import Progress
from tests.ml.test_disruption_features import _daily, _profiles
from tests.ml.test_port_forecast import _week, _weekly


def test_goal_check_picks_the_better_rule() -> None:
    n = 200
    y = [i % 20 == 0 for i in range(n)]
    rows = pl.DataFrame(
        {
            "origin_week": [_week(0)] * n,
            "horizon": [1] * n,
            "port_id": [f"p{i:03d}" for i in range(n)],
            "y": y,
            "big": [False] * n,
            MODEL: [float(v) for v in y],  # perfect
            "last_z": [float(v) * 0.5 + (i % 7) for i, v in enumerate(y)],
            **{r: [0.0] * n for r in RULES if r != "last_z"},
        }
    )
    row = goal_check(rows).row(0, named=True)
    assert row["ap_model"] == pytest.approx(1.0)
    assert row["best_rule"] == "last_z" and row["ratio"] > 1
    by = goal_check(rows, ["horizon"])
    assert by.columns[0] == "horizon" and by.height == 1


def test_pr_curve_and_calibration() -> None:
    rows = pl.DataFrame({"m": [0.9, 0.8, 0.1, 0.05], "y": [True, False, True, False]})
    curve = pr_curve(rows, ["m"], points=5)
    first = curve[0]
    assert first["precision"] == 1.0 and first["recall"] == 0.5
    assert curve[-1]["share"] <= 1
    cal = calibration(rows.rename({"m": MODEL}))
    assert cal["n"].sum() == 4
    top = cal.filter(pl.col("bin") == "0.4-1").row(0, named=True)
    assert top["n"] == 2 and top["actual"] == pytest.approx(0.5)


def test_train_and_backtest_scores_every_eval_row(tmp_path) -> None:  # noqa: ANN001
    pytest.importorskip("lightgbm")
    # Ports that collapse now and then, with a nearby port dropping first.
    ports = {f"p{k}": float(k) * 0.5 for k in range(6)}
    weekly = pl.concat(
        [_weekly(200, lambda i, k=k: 5 if (i + 7 * k) % 23 == 0 else 50 + (i % 3), p)
         for k, p in enumerate(ports)]
    )
    no_events = pl.DataFrame([{"event_id": "e", "event_type": "TC", "alert_level": "Orange",
                               "from_date": datetime(2000, 1, 3), "latitude": 0.0,
                               "longitude": 0.0, "affected_ports": None}])
    origins = [_week(w) for w in range(70, 197)]
    fx = build_features(weekly, no_events, _profiles(ports), _daily(1500, lambda i: 10), origins)
    config = TrainConfig(eval_start=_week(160), retrain_weeks=12, valid_weeks=20,
                         num_boost_round=30, early_stopping_rounds=10)
    config.params.update(min_data_in_leaf=5)
    progress = Progress(tmp_path, config)
    out = train_and_backtest(fx, 200.0, config, progress)
    expected = fx.filter(pl.col("origin_week") >= _week(160))
    assert out.height == expected.height
    assert out.select("origin_week", "horizon", "port_id").is_duplicated().sum() == 0
    assert out[MODEL].is_between(0, 1).all()
    state = json.loads((tmp_path / "progress.json").read_text(encoding="utf-8"))
    assert state["status"] == "done" and len(state["folds"]) == state["n_folds"] == 4
    assert state["scores"]["all"][0]["n"] == out.height
