from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from src.ml.route_shift.episodes import BAB, CAPE, SHIP_TYPES, SUEZ, weekly_transits
from src.ml.route_shift.response import (
    FORECASTS,
    ONSET,
    RETURN,
    fit,
    forecast,
    freeze,
    lag_curve,
    load_frozen,
    score,
)

FIRST, LAST = date(2019, 1, 7), date(2026, 10, 4)


def _ramp(d: date, start: date, weeks: int) -> float:
    """0 before start, rising linearly to 1 over ``weeks``."""
    return float(np.clip(((d - start).days / 7 + 1) / weeks, 0, 1))


def _level(cp: str, st: str, d: date) -> float:
    on = {"container": _ramp(d, ONSET.start, 1), "tanker": _ramp(d, ONSET.start, 6),
          "dry_bulk": _ramp(d, ONSET.start, 8)}.get(st, 0.0)
    back = _ramp(d, RETURN.start, 1) if st == "container" else 0.0  # followers lag
    if cp == CAPE:
        return 10 + 10 * on - 3 * back
    return 20 - 10 * on + 3 * back  # Suez and Bab el-Mandeb


@pytest.fixture(scope="module")
def weekly() -> pl.DataFrame:
    rng = np.random.default_rng(0)
    rows = []
    for k in range((LAST - FIRST).days + 1):
        d = FIRST + timedelta(days=k)
        for cp in (SUEZ, BAB, CAPE):
            row: dict[str, object] = {"transit_date": d, "chokepoint_name": cp}
            for st in SHIP_TYPES:
                row[f"n_{st}"] = int(round(_level(cp, st, d) + rng.normal(0, 0.5)))
            rows.append(row)
    return weekly_transits(pl.DataFrame(rows))


def test_lag_curve_rises_to_one_and_never_falls() -> None:
    path = np.array([0, 2, 5, 4, 8, 10, 11, 9, 10, np.nan])
    g = lag_curve(path, 10.0)
    assert np.all(np.diff(g) >= 0) and g.max() == 1 and g[0] < 0.3


def test_fit_reads_the_switch_and_the_share_back(weekly: pl.DataFrame) -> None:
    f = fit(weekly)
    assert abs(f.gain["container"] - 10) < 0.5 and abs(f.gain["tanker"] - 10) < 0.5
    assert f.curve["container"][1] > 0.95 and f.curve["tanker"][2] < 0.7
    assert abs(f.share - 0.3) < 0.05
    assert abs(f.now["container"] + 3) < 0.5 and abs(f.now["tanker"]) < 0.5


def test_forecast_moves_only_the_followers(weekly: pl.DataFrame) -> None:
    f = fit(weekly)
    fc = forecast(weekly, f)
    assert fc.height == 3 * 8 and fc["week_start"].min() == date(2026, 10, 5)
    box = fc.filter(pl.col("ship_type") == "container")
    assert (box["model"] - box["no_change"]).abs().max() < 1e-9
    tank = fc.filter(pl.col("ship_type") == "tanker")
    # Tankers haven't moved; the model says they lose share × gain (~3 a day).
    assert ((tank["no_change"] - tank["model"]) - 3).abs().max() < 0.6
    # All back: the Cape loses the rest of its gain (10 − 3 = 7 for containers).
    assert ((box["no_change"] - box["all_back"]) - 7).abs().max() < 0.6


def test_freeze_and_score_round_trip(weekly: pl.DataFrame, tmp_path) -> None:  # noqa: ANN001
    path = tmp_path / "fc.json"
    freeze(weekly, path)
    frozen = load_frozen(path)
    assert set(FORECASTS) <= set(frozen.columns)
    # Pretend the next two weeks came in at exactly the model's numbers.
    actual = (frozen.filter(pl.col("week_start") <= date(2026, 10, 12))
              .select("week_start", "ship_type", pl.col("model").alias("transits"))
              .with_columns(pl.lit(CAPE).alias("chokepoint")))
    s = score(frozen, actual).filter(pl.col("ship_type") == "all").row(0, named=True)
    assert s["weeks"] == 6 and s["model"] < 1e-9 and s["no_change"] > 0


def test_panel_lines_up_actual_and_forecast_weeks(weekly: pl.DataFrame, tmp_path) -> None:  # noqa: ANN001
    from src.ml.route_shift.panel import panel

    path = tmp_path / "fc.json"
    freeze(weekly, path)
    out = panel(weekly, path)
    n = len(out["weeks"])
    assert out["first"] == "2026-10-05" and out["horizon"] == 8
    for d in out["types"].values():
        assert all(len(v) == n for v in d.values())
        assert d["actual"][-1] is None and d["model"][0] is None  # future / before the test
    # Nothing scored yet: only the empty "all" row, with no errors.
    assert out["scores"] == [{"ship_type": "all", "weeks": 0, "model": None,
                              "no_change": None, "all_back": None}]
