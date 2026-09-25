from __future__ import annotations

import json
import re
from datetime import date, datetime

import numpy as np
import polars as pl
import pytest

from src.ml.disruption_warning import dashboard
from src.ml.disruption_warning.features import FEATURES
from src.ml.disruption_warning.predict import (
    REASONS,
    flag_top,
    forecast,
    merge_history,
    reasons,
    to_history,
    track_record,
)
from src.ml.disruption_warning.train import TrainConfig, apply_platt, fit_platt
from tests.ml.test_disruption_features import _daily, _profiles
from tests.ml.test_port_forecast import _week, _weekly


def test_reasons_names_the_groups_that_push_up() -> None:
    contrib = np.zeros((2, len(FEATURES) + 1))
    contrib[0, FEATURES.index("origin_z")] = 1.0
    contrib[0, FEATURES.index("event_n")] = 0.5
    contrib[1, FEATURES.index("choke1_km")] = -2.0  # pushes down: never a reason
    out = reasons(contrib)
    assert out[0] == ["calls already falling", "storm or disaster alert nearby"]
    assert out[1] == []
    assert set(REASONS)  # every group has a name


def test_flag_top_takes_one_percent_per_origin_and_horizon() -> None:
    rows = pl.DataFrame({
        "origin_week": [date(2026, 1, 5)] * 300,
        "horizon": [1] * 150 + [2] * 150,
        "chance": list(range(150)) * 2,
    })
    flagged = rows.with_columns(flag_top(rows).alias("f")).filter(pl.col("f"))
    assert flagged.height == 4  # ceil(1% of 150) = 2 per horizon
    assert set(flagged["chance"]) == {149, 148}


def test_platt_fixes_under_confident_scores() -> None:
    rng = np.random.default_rng(0)
    truth = rng.uniform(0.001, 0.3, 50_000)
    y = rng.uniform(size=truth.size) < truth
    said = truth / 2  # under-predicts by half
    a, b = fit_platt(said, y)
    fixed = apply_platt(said, a, b)
    assert abs(fixed.mean() - y.mean()) < 0.005
    assert np.all(np.diff(fixed[np.argsort(said)]) >= -1e-12)  # ranking kept


def _hist(origin: date, chance: float) -> pl.DataFrame:
    rows = pl.DataFrame({"origin_week": [origin], "target_week": [origin], "horizon": [1],
                         "port_id": ["p0"], "chance": [chance]})
    return to_history(rows, "live")


def test_merge_history_replaces_a_rerun_week() -> None:
    old = pl.concat([_hist(date(2026, 1, 5), 0.1), _hist(date(2026, 1, 12), 0.2)])
    out = merge_history(old, _hist(date(2026, 1, 12), 0.9))
    assert out.height == 2 and out["chance"].to_list() == [0.1, 0.9]
    assert merge_history(None, old).height == 2


def test_track_record_keeps_only_known_outcomes() -> None:
    hist = pl.concat([_hist(date(2026, 1, 5), 0.1), _hist(date(2026, 1, 12), 0.2)])
    labels = pl.DataFrame({"week_start": [date(2026, 1, 5), date(2026, 1, 12)],
                           "port_id": ["p0", "p0"], "is_disruption": [True, None],
                           "drop_pct": [0.6, None]})
    rec = track_record(hist, labels)
    assert rec.height == 1 and rec["happened"].to_list() == [True]


def test_forecast_scores_the_newest_week_and_renders(tmp_path) -> None:  # noqa: ANN001
    pytest.importorskip("lightgbm")
    ports = {f"p{k}": float(k) * 0.5 for k in range(6)}
    weekly = pl.concat(
        [_weekly(200, lambda i, k=k: 5 if (i + 7 * k) % 23 == 0 else 50 + (i % 3), p)
         for k, p in enumerate(ports)]
    )
    events = pl.DataFrame([{"event_id": "e", "event_type": "TC", "alert_level": "Orange",
                            "from_date": datetime(2000, 1, 3), "latitude": 0.0,
                            "longitude": 0.0, "affected_ports": None}])
    profiles = _profiles(ports).with_columns(
        pl.col("port_id").alias("port_name"), pl.lit("X").alias("country"),
        pl.lit("Food").alias("industry_top1"),
    )
    config = TrainConfig(train_start=_week(70), num_boost_round=30, early_stopping_rounds=10,
                         valid_weeks=20)
    config.params.update(min_data_in_leaf=5)
    fc = forecast(weekly, events, profiles, _daily(1500, lambda i: 10), config)
    assert fc.origin == _week(199)
    w = fc.warnings
    assert w.height == 3 * len(ports)
    assert w["chance"].is_between(0, 1).all()
    assert sorted(w["target_week"].unique()) == [_week(200), _week(201), _week(202)]
    history = to_history(w, "live")
    page = dashboard.render(fc, pl.DataFrame(), history)
    blob = re.search(r'<script id="data" type="application/json">(.*?)</script>', page, re.S)
    assert blob is not None
    data = json.loads(blob.group(1))
    assert data["origin"] == _week(199).isoformat() and len(data["ports"]) == len(ports)
    assert data["record"]["summary"] is None
