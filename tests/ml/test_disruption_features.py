from __future__ import annotations

from datetime import date, datetime, timedelta

import polars as pl
import pytest

from src.ml.disruption_warning.baselines import RELEASE_LAG_DAYS, prepare_events
from src.ml.disruption_warning.features import (
    CHOKEPOINT_LAG_DAYS,
    FEATURES,
    KEYS,
    build_features,
    chokepoint_features,
    chokepoint_state,
    drop_state,
    event_features,
    region_features,
    year_ago_drops,
)
from src.ml.disruption_warning.labels import label_drops
from src.ml.port_forecast.baselines import weekly_grid
from tests.ml.test_port_forecast import _week, _weekly


def _profiles(lats: dict[str, float]) -> pl.DataFrame:
    ports = list(lats)
    return pl.DataFrame(
        {
            "port_id": ports,
            "continent": ["Asia"] * len(ports),
            "latitude": [lats[p] for p in ports],
            "longitude": [0.0] * len(ports),
            "vessel_count_total": [100] * len(ports),
            **{f"vessel_count_{t}": [20] * len(ports) for t in
               ("container", "dry_bulk", "general_cargo", "roro", "tanker")},
        }
    )


def _steady(i: int) -> float:
    return 50 + (i % 3)


def _collapse(weeks: tuple[int, ...]):  # noqa: ANN202
    return lambda i: 5 if i in weeks else _steady(i)


def _at(labels: pl.DataFrame, frame: pl.DataFrame, week: int, col: str = "origin_week") -> dict:
    return frame.filter(pl.col(col) == _week(week)).row(0, named=True)


def test_drop_state_counts_back_from_the_origin() -> None:
    labels = label_drops(weekly_grid(_weekly(120, _collapse((80, 100)))))
    state = drop_state(labels)
    at = {w: _at(labels, state, w) for w in (79, 80, 81, 105)}
    assert at[79]["weeks_since_drop"] == 156 and at[79]["drops_52"] == 0
    assert at[80]["weeks_since_drop"] == 0 and at[81]["weeks_since_drop"] == 1
    assert at[81]["z_lag_1"] < -2.5 and at[81]["min_z_4"] < -2.5
    assert at[105]["weeks_since_drop"] == 5 and at[105]["drops_52"] == 2


def test_year_ago_drop_lines_up_with_the_target_week() -> None:
    labels = label_drops(weekly_grid(_weekly(160, _collapse((80,)))))
    ly = year_ago_drops(labels)
    flagged = ly.filter(pl.col("dropped_near_target_last_year"))["target_week"].to_list()
    assert flagged == [_week(131), _week(132), _week(133)]
    # The furthest target (origin + 3) uses weeks up to target - 51: all known.
    assert max(flagged) - timedelta(weeks=51) <= _week(82)


def _daily(n_days: int, calls, cp: str = "c1", lat: float = 0.0) -> pl.DataFrame:  # noqa: ANN001
    start = date(2020, 1, 1)
    return pl.DataFrame(
        {
            "chokepoint_id": [cp] * n_days,
            "transit_date": [start + timedelta(days=i) for i in range(n_days)],
            "latitude": [lat] * n_days,
            "longitude": [0.0] * n_days,
            "n_total": [int(calls(i)) for i in range(n_days)],
        }
    )


def test_chokepoint_changes() -> None:
    # 10 a day, halved from day 400 on.
    state = chokepoint_state(_daily(420, lambda i: 5 if i >= 400 else 10))
    day = lambda i: date(2020, 1, 1) + timedelta(days=i)  # noqa: E731
    row = state.filter(pl.col("day") == day(406)).row(0, named=True)
    assert row["vs_year_ago"] == pytest.approx(-0.5)
    assert row["vs_prior_28d"] == pytest.approx(-0.5)
    assert state.filter(pl.col("day") == day(399))["vs_year_ago"][0] == pytest.approx(0)
    assert state.filter(pl.col("day") == day(100))["vs_year_ago"][0] is None


def test_chokepoints_are_read_two_days_before_release() -> None:
    origin = date(2021, 2, 1)
    seen = origin + timedelta(days=RELEASE_LAG_DAYS - CHOKEPOINT_LAG_DAYS)
    cut = (seen - date(2020, 1, 1)).days
    profiles = _profiles({"p1": 1.0})
    # A collapse starting the day after the read day is not visible...
    late = _daily(600, lambda i: 0 if i > cut else 10)
    near = chokepoint_features(late, profiles, [origin]).row(0, named=True)
    assert near["choke1_vs_prior_28d"] == pytest.approx(0)
    assert near["choke1_km"] == pytest.approx(111, rel=0.01)
    # ...one starting on it is.
    early = _daily(600, lambda i: 0 if i >= cut else 10)
    assert chokepoint_features(early, profiles, [origin])["choke1_vs_prior_28d"][0] < 0


def test_nearest_chokepoints_in_order() -> None:
    daily = pl.concat([_daily(400, lambda i: 10, "far", 20.0), _daily(400, lambda i: 10, "near")])
    row = chokepoint_features(daily, _profiles({"p1": 1.0}), [date(2021, 1, 4)]).row(0, named=True)
    assert row["choke1_km"] < row["choke2_km"]
    assert row["choke2_km"] == pytest.approx(19 * 111.2, rel=0.01)


def test_region_share_excludes_the_port_itself() -> None:
    weekly = pl.concat(
        [
            _weekly(100, _collapse((80,)), "p1"),
            _weekly(100, _collapse((80,)), "p2"),  # ~111 km from p1
            _weekly(100, _steady, "p3"),  # ~111 km from p1
            _weekly(100, _collapse((80,)), "p4"),  # ~2,200 km away
        ]
    )
    labels = label_drops(weekly_grid(weekly))
    profiles = _profiles({"p1": 0.0, "p2": 1.0, "p3": -1.0, "p4": 20.0})
    reg = region_features(labels, profiles, [_week(80)])
    p1 = reg.filter(pl.col("port_id") == "p1").row(0, named=True)
    assert p1["region_ports"] == 2 and p1["region_drop_share"] == pytest.approx(0.5)
    assert p1["world_drop_share"] == pytest.approx(0.75)
    p4 = reg.filter(pl.col("port_id") == "p4").row(0, named=True)
    assert p4["region_ports"] == 0 and p4["region_drop_share"] is None


def _event(eid: str, when: date, level: str, lat: float, kind: str = "TC") -> dict:
    return {"event_id": eid, "event_type": kind, "alert_level": level,
            "from_date": datetime.combine(when, datetime.min.time()), "latitude": lat,
            "longitude": 0.0, "affected_ports": None}


def test_event_features() -> None:
    origin = _week(79)
    release = origin + timedelta(days=RELEASE_LAG_DAYS)
    events = prepare_events(
        pl.DataFrame(
            [
                _event("red", release - timedelta(days=3), "RED", 1.0),
                _event("near", release - timedelta(days=1), "Orange", 0.1, "EQ"),
                _event("late", release + timedelta(days=1), "RED", 0.0),
            ]
        )
    )
    rows = pl.DataFrame(
        {"origin_week": [origin], "target_week": [origin + timedelta(weeks=2)],
         "horizon": pl.Series([2], dtype=pl.Int8), "port_id": ["p1"]}
    )
    row = event_features(rows, events, _profiles({"p1": 0.0})).row(0, named=True)
    assert row["event_n"] == 2 and row["event_n_red"] == 1
    assert row["event_red_km"] == pytest.approx(111, rel=0.01)
    assert row["event_type"] == "EQ"  # the nearest one
    assert row["event_days_before_target"] == 0  # "near" starts on the target Monday


def _build(calls) -> pl.DataFrame:  # noqa: ANN001
    weekly = pl.concat([_weekly(160, calls, "p1"), _weekly(160, _steady, "p2")])
    no_events = pl.DataFrame([_event("e", date(2000, 1, 3), "Orange", 0.0)])
    choke = _daily(1200, lambda i: 10)
    profiles = _profiles({"p1": 0.0, "p2": 1.0})
    return build_features(weekly, no_events, profiles, choke, [_week(w) for w in (130, 140)])


def test_build_features_has_every_column_and_no_future() -> None:
    base = _build(_steady)
    assert base.columns == [*KEYS, "y", "big", "size_band", *FEATURES[1:]]
    assert base.select(KEYS).is_duplicated().sum() == 0
    # A different future after origin 130 changes only rows from later origins
    # and the targets.
    other = _build(_collapse((131, 132, 150)))
    features = [f for f in FEATURES if f != "horizon"]
    for frame in (base, other):
        assert frame.filter(pl.col("origin_week") == _week(130)).height == 6
    same = base.filter(pl.col("origin_week") == _week(130)).select(KEYS, *features)
    assert same.equals(other.filter(pl.col("origin_week") == _week(130)).select(KEYS, *features))
    y = other.filter((pl.col("origin_week") == _week(130)) & (pl.col("port_id") == "p1"))
    assert y.sort("horizon")["y"].to_list() == [True, True, False]
