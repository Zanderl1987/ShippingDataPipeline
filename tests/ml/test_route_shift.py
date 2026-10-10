from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import polars as pl

from src.ml.route_shift.episodes import (
    SHIP_TYPES,
    Episode,
    _weeks_to,
    build_series,
    measure,
    placebo_starts,
    transfers,
    weekly_transits,
)

FIRST = date(2019, 1, 7)  # a Monday
STEP = date(2022, 6, 6)


def _daily(level: dict[str, object], days: int = 5 * 365, seed: int = 0) -> pl.DataFrame:
    """Daily transits for chokepoints A and B; ``level[cp](day)`` is the container mean."""
    rng = np.random.default_rng(seed)
    rows = []
    for cp, f in level.items():
        for k in range(days):
            d = FIRST + timedelta(days=k)
            row: dict[str, object] = {"transit_date": d, "chokepoint_name": cp}
            for t in SHIP_TYPES:
                row[f"n_{t}"] = 5 + int(rng.integers(0, 2))
            row["n_container"] = max(0, round(f(d) + rng.normal(0, 1)))  # type: ignore[operator]
            rows.append(row)
    return pl.DataFrame(rows)


def test_weekly_transits_drops_part_weeks_and_keeps_the_newest_copy() -> None:
    daily = _daily({"A": lambda d: 10.0}, days=10)  # one full week, then 3 days
    fixed = daily.head(1).with_columns(pl.lit(99, dtype=pl.Int64).alias("n_container"))
    weekly = weekly_transits(pl.concat([daily, fixed]))
    box = weekly.filter(pl.col("ship_type") == "container")
    assert box["week_start"].to_list() == [FIRST]
    expect = (99 + daily["n_container"][1:7].sum()) / 7
    assert math.isclose(box["transits"][0], expect)


def test_measure_finds_a_route_switch_and_one_for_one_transfer() -> None:
    daily = _daily({"A": lambda d: 20.0 if d < STEP else 10.0,
                    "B": lambda d: 10.0 if d < STEP else 20.0})
    ep = Episode("x", "test", STEP, ("A",), ("B",))
    out = measure(weekly_transits(daily), [ep])
    box = {r["chokepoint"]: r for r in out.filter(pl.col("ship_type") == "container")
           .iter_rows(named=True)}
    assert abs(box["A"]["change"] + 10) < 0.5 and abs(box["B"]["change"] - 10) < 0.5
    assert box["A"]["z"] < -5 and box["A"]["half_weeks"] == 0 and box["A"]["settled"]
    assert box["B"]["role"] == "alternative"
    # Tankers didn't move: not significant, so no timing.
    tank = out.filter((pl.col("chokepoint") == "A") & (pl.col("ship_type") == "tanker")).row(
        0, named=True)
    assert abs(tank["z"]) < 2 and tank["half_weeks"] is None
    moved = transfers(out, [ep]).filter(pl.col("ship_type") == "container").row(0, named=True)
    assert abs(moved["ratio"] - 1) < 0.1


def test_live_episode_uses_its_newest_weeks() -> None:
    end = FIRST + timedelta(days=5 * 365)
    start = end - timedelta(weeks=5)
    start -= timedelta(days=start.weekday())
    daily = _daily({"A": lambda d: 10.0 if d < start else 16.0})
    out = measure(weekly_transits(daily), [Episode("live", "live", start, ("A",))])
    row = out.filter(pl.col("ship_type") == "container").row(0, named=True)
    assert not row["settled"] and row["weeks_seen"] <= 6
    assert abs(row["change"] - 6) < 0.6


def test_seasonal_method_removes_a_yearly_cycle() -> None:
    cycle = lambda d: 20 + 6 * math.sin(2 * math.pi * d.toordinal() / 364)  # noqa: E731
    out = measure(weekly_transits(_daily({"A": cycle})), [Episode("x", "x", STEP, ("A",))])
    row = out.filter(pl.col("ship_type") == "container").row(0, named=True)
    assert row["method"] == "seasonal" and abs(row["z"]) < 2


def test_placebos_skip_weeks_an_episode_could_touch() -> None:
    weekly = weekly_transits(_daily({"A": lambda d: 10.0}))
    s = build_series(weekly)[("A", "container")]
    ep = Episode("x", "x", STEP, ("A",))
    lo, hi = ep.affected()
    for seasonal in (False, True):
        starts = placebo_starts(s, "A", [ep], seasonal)
        assert starts.size > 0
        back = 10 + (52 if seasonal else 0)
        for i in starts:
            assert s.weeks[i + 26] < lo or s.weeks[i - back] > hi
    # An episode elsewhere doesn't remove any.
    other = Episode("y", "y", STEP, ("Z",))
    assert placebo_starts(s, "A", [other], False).size > placebo_starts(s, "A", [ep], False).size


def test_weeks_to_uses_a_three_week_average() -> None:
    path = np.array([0.0, 1, 2, 4, 6, 8, 10, 10, 10, np.nan])
    assert _weeks_to(path, 10.0, 0.5) == 4  # (4 + 6 + 8) / 3 = 6
    assert _weeks_to(path, 10.0, 0.9) == 6  # (8 + 10 + 10) / 3
    assert _weeks_to(-path, -10.0, 0.5) == 4
    assert _weeks_to(np.full(3, np.nan), 1.0, 0.5) is None
