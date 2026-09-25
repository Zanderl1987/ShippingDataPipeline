from __future__ import annotations

from datetime import datetime, timedelta

import polars as pl
import pytest

from src.ml.disruption_warning.baselines import (
    RELEASE_LAG_DAYS,
    event_exposure,
    prepare_events,
    rule_scores,
    warning_rows,
)
from src.ml.disruption_warning.evaluate import average_precision, score
from src.ml.disruption_warning.labels import label_drops
from src.ml.port_forecast.baselines import weekly_grid
from tests.ml.test_port_forecast import _week, _weekly

PROFILES = pl.DataFrame(
    {"port_id": ["p1", "p2"], "latitude": [0.0, 10.0], "longitude": [0.0, 0.0]}
)


def _event(eid: str, when: datetime, level: str = "Orange", lat: float = 0.0,
           ports: str | None = None, source: str = "geopulse", kind: str = "TC") -> dict:
    return {"event_id": eid, "event_type": kind, "alert_level": level, "from_date": when,
            "latitude": lat, "longitude": 0.0, "affected_ports": ports, "source": source}


def _events(*rows: dict) -> pl.DataFrame:
    return pl.DataFrame(list(rows))


def test_prepare_events_merges_the_two_copies() -> None:
    day = datetime(2022, 3, 2)
    raw = _events(
        _event("e1", day, "Orange"),
        _event("e1", day, "RED", ports="p1; p9", source="disruptions"),
        _event("e2", day, kind="DR"),  # drought: dropped
    )
    ev = prepare_events(raw)
    assert ev.height == 1
    row = ev.row(0, named=True)
    assert row["level"] == "RED" and row["ports"] == ["p1", "p9"]
    assert row["from_date"] == day.date()


def test_exposure_distance_listing_and_weeks() -> None:
    # Wednesday of week 100; p1 at the centre, p2 ~1,112 km away but listed.
    start = datetime.combine(_week(100) + timedelta(days=2), datetime.min.time())
    ev = prepare_events(_events(_event("e1", start, "RED", ports="p2")))
    exp = event_exposure(ev, PROFILES, max_km=500)
    p1 = exp.filter(pl.col("port_id") == "p1")
    assert p1["target_week"].to_list() == [_week(100), _week(101), _week(102)]
    assert p1["km"][0] == pytest.approx(0) and not p1["listed"][0]
    p2 = exp.filter(pl.col("port_id") == "p2").row(0, named=True)
    assert p2["listed"] and p2["km"] == pytest.approx(1112, rel=0.01)
    # Not listed and too far: gone.
    assert event_exposure(prepare_events(_events(_event("e2", start, lat=10.0))),
                          PROFILES.filter(pl.col("port_id") == "p1"), max_km=500).height == 0


def _labels() -> pl.DataFrame:
    # A steady port that collapses in weeks 80-81 (and never the year before).
    return label_drops(weekly_grid(_weekly(100, lambda i: 5 if i in (80, 81) else 50 + (i % 3))))


def test_rows_use_the_origin_and_the_target_week() -> None:
    no_events = event_exposure(prepare_events(_events(_event("e", datetime(2000, 1, 3)))), PROFILES)
    rows = warning_rows(_labels(), no_events, origins=[_week(79), _week(80)])
    got = {(r["origin_week"], r["horizon"]): r for r in rows.iter_rows(named=True)}
    assert got[(_week(79), 1)]["y"] and not got[(_week(79), 1)]["origin_drop"]
    assert got[(_week(80), 1)]["y"] and got[(_week(80), 1)]["origin_drop"]  # persistence
    assert not got[(_week(80), 2)]["y"]
    assert got[(_week(80), 1)]["origin_z"] < -2.5
    assert rows["event_km"].null_count() == rows.height


def test_base_rate_never_sees_past_the_origin() -> None:
    no_events = event_exposure(prepare_events(_events(_event("e", datetime(2000, 1, 3)))), PROFILES)
    steady = label_drops(weekly_grid(_weekly(100, lambda i: 50 + (i % 3))))

    def rate(labels: pl.DataFrame, origin: int) -> float:
        return warning_rows(labels, no_events, origins=[_week(origin)])["base_rate"][0]

    # Same history up to week 79, different future: same rate at origin 79.
    assert rate(_labels(), 79) == rate(steady, 79) == 0.0
    assert rate(_labels(), 82) > 0.0


def test_only_events_out_by_release_day_count() -> None:
    labels = _labels()
    release = _week(79) + timedelta(days=RELEASE_LAG_DAYS)
    at = lambda d: datetime.combine(d, datetime.min.time())  # noqa: E731
    for when, known in ((release, True), (release + timedelta(days=1), False)):
        exp = event_exposure(prepare_events(_events(_event("e", at(when)))), PROFILES)
        rows = warning_rows(labels, exp, origins=[_week(79)])
        # The event's own week is horizon 2 (release day is in it) and 3.
        h3 = rows.filter(pl.col("horizon") == 3).row(0, named=True)
        assert (h3["event_km"] is not None) is known


def test_flags_rank_above_the_tie_break() -> None:
    rows = pl.DataFrame(
        {"base_rate": [0.9, 0.01], "origin_z": [0.0, -3.0], "origin_drop": [False, True],
         "event_km": [None, 150.0], "event_listed": [False, False]}
    )
    out = rule_scores(rows, radius_km=200)
    assert out["persistence"][1] > out["persistence"][0]
    assert out["gdacs"][1] > out["gdacs"][0]
    assert rule_scores(rows, radius_km=100)["gdacs"][1] < out["gdacs"][0]
    assert out["last_z"].to_list() == [0.0, 3.0]


def test_average_precision() -> None:
    rows = pl.DataFrame({"m": [0.9, 0.8, 0.7, 0.6], "y": [True, False, True, False]})
    assert average_precision(rows, "m") == pytest.approx((1 + 2 / 3) / 2)
    # One block of ties scores the base rate: no credit for a lucky order.
    tied = rows.with_columns(pl.lit(1.0).alias("m"))
    assert average_precision(tied, "m") == pytest.approx(0.5)


def test_budget_flags_an_exact_share_each_week() -> None:
    n = 250  # 1% -> 3 alerts per week (rounded up)
    rows = pl.DataFrame(
        {
            "origin_week": [_week(0)] * n + [_week(1)] * n,
            "horizon": [1] * (2 * n),
            "port_id": [f"p{i:03d}" for i in range(n)] * 2,
            "m": [1.0] * n + [float(i) for i in range(n)],  # week 0 all tied
            "y": [i in (0, 1, 2) for i in range(n)] + [i == n - 1 for i in range(n)],
            "big": [False] * (2 * n),
        }
    )
    out = score(rows, ["m"], by=["origin_week"]).sort("origin_week")
    assert out["precision"].to_list() == [pytest.approx(1.0), pytest.approx(1 / 3)]
    assert out["recall"].to_list() == [1.0, 1.0]
