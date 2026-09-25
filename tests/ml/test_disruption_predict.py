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
    DRIVER_GROUPS,
    REASONS,
    Forecast,
    flag_top,
    forecast,
    merge_history,
    nearby_events,
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
    # Drill-down: every group's push, a year of calls per port, the coastline.
    assert all(len(r["drivers"]) == len(DRIVER_GROUPS) for r in data["chances"]["2"])
    detail = data["detail"]
    assert set(detail["ports"]) == set(ports)
    one = detail["ports"]["p0"]
    assert len(one["calls"]) == len(one["usual"]) == len(one["marks"]) == len(detail["weeks"])
    assert "__LAND__" not in page and 'class="land" d="M' in page


def test_nearby_events_keeps_recent_close_or_listed_events() -> None:
    raw = pl.DataFrame([
        # near p0, recent; listed twice (PortWatch + GeoPulse), once as RED
        {"event_id": "a", "event_name": "Storm A", "event_type": "TC", "alert_level": "Orange",
         "from_date": datetime(2026, 3, 1), "latitude": 0.0, "longitude": 1.0,
         "affected_ports": None},
        {"event_id": "a", "event_name": None, "event_type": "TC", "alert_level": "Red",
         "from_date": datetime(2026, 3, 2), "latitude": None, "longitude": None,
         "affected_ports": None},
        # far away but names p1
        {"event_id": "b", "event_name": "Quake B", "event_type": "EQ", "alert_level": "Orange",
         "from_date": datetime(2026, 3, 5), "latitude": 50.0, "longitude": 50.0,
         "affected_ports": "p9; p1"},
        # too old, and a drought
        {"event_id": "c", "event_name": "Old", "event_type": "TC", "alert_level": "Red",
         "from_date": datetime(2025, 1, 1), "latitude": 0.0, "longitude": 0.0,
         "affected_ports": None},
        {"event_id": "d", "event_name": "Dry", "event_type": "DR", "alert_level": "Red",
         "from_date": datetime(2026, 3, 5), "latitude": 0.0, "longitude": 0.0,
         "affected_ports": None},
    ])
    ports = pl.DataFrame({"port_id": ["p0", "p1"], "latitude": [0.0, -40.0],
                          "longitude": [0.0, 120.0]})
    out = nearby_events(raw, ports, date(2026, 3, 20))
    got = {(r["port_id"], r["name"], r["level"]) for r in out.iter_rows(named=True)}
    assert got == {("p0", "Storm A", "RED"), ("p1", "Quake B", "ORANGE")}
    assert nearby_events(raw.head(0), ports, date(2026, 3, 20)).is_empty()


def test_port_details_line_up_weeks_and_count_the_record() -> None:
    w = [date(2026, 1, 5), date(2026, 1, 12)]
    calls = pl.DataFrame({"port_id": ["p0", "p0", "p1"], "week_start": [w[0], w[1], w[1]],
                          "y": [50.0, 10.0, 7.0], "base": [48.0, 49.0, 7.0],
                          "is_disruption": [False, True, None],
                          "is_seasonal": [False, False, None], "drop_pct": [0.0, 0.8, None]})
    fc = Forecast(origin=w[1], release=w[1], warnings=pl.DataFrame(), rounds=1,
                  trained_rows=1, calls=calls,
                  past=calls.filter(pl.col("is_disruption").fill_null(False))
                  .select("port_id", "week_start", "drop_pct"))
    record = pl.DataFrame({"port_id": ["p0", "p0", "p0"], "target_week": [w[1]] * 3,
                           "flagged": [True, False, True], "happened": [True] * 3})
    out = dashboard.port_details(fc, record)
    assert out["weeks"] == [d.isoformat() for d in w]
    p0, p1 = out["ports"]["p0"], out["ports"]["p1"]
    assert p0["calls"] == [50.0, 10.0] and p0["marks"] == ".D"
    assert p1["calls"] == [None, 7.0] and p1["marks"] == ".."
    assert p0["past_n"] == 1 and p0["past"] == [["2026-01-12", 0.8]]
    assert p0["record"] == [2, 2, 1, 1]  # 2 alerts, both right; 1 disruption week, caught
