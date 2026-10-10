from __future__ import annotations

import math
from datetime import date

import numpy as np
import polars as pl
import pytest

from src.ml.oil_nowcast.us import (
    COASTS,
    MODELS,
    backtest,
    build_panel,
    census_series,
    choose,
    scores,
    target_met,
)


def _months(n: int, first: date = date(2019, 1, 1)) -> list[date]:
    return pl.date_range(first, date(2040, 1, 1), "1mo", eager=True).head(n).to_list()


def _inputs(n: int = 90, share: float = 0.5, seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Census all-oil exports = share × PortWatch exports, PortWatch a noisy wave."""
    rng = np.random.default_rng(seed)
    months = _months(n)
    pw_rows, census_rows = [], []
    for k, m in enumerate(months):
        coast = {c: 1e6 * (1 + 0.3 * math.sin(k / 3 + j)) * (1 + 0.05 * rng.normal())
                 for j, c in enumerate(COASTS)}
        pw_rows += [{"month": m, "coast": c, "flow": "X", "tonnes": v} for c, v in coast.items()]
        census_rows.append({"month": m, "series": "all_oil_exports",
                            "tonnes": share * sum(coast.values())})
    return pl.DataFrame(census_rows), pl.DataFrame(pw_rows)


def test_census_series_sums_codes_per_flow() -> None:
    raw = pl.DataFrame({
        "month": [date(2025, 1, 1)] * 4,
        "flow_code": ["X", "X", "M", "X"],
        "hs4": ["2709", "2710", "2709", "2701"],  # 2701 (coal) is in no series
        "tonnes": [1.0, 2.0, 4.0, 8.0],
    })
    out = dict(census_series(raw).select("series", "tonnes").iter_rows())
    assert out == {"all_oil_exports": 3.0, "all_oil_imports": 4.0, "crude_exports": 1.0,
                   "crude_imports": 4.0}


def test_build_panel_keeps_a_month_portwatch_missed() -> None:
    census, pw = _inputs(6)
    gap = date(2019, 3, 1)
    p = build_panel(census, pw.filter(pl.col("month") != gap), "all_oil_exports")
    assert p.months == _months(6)
    assert np.isnan(p.coasts[2]).all() and np.isfinite(p.census[2])
    rows = backtest(p, "all_oil_exports")
    assert gap not in rows["month"].to_list()  # nothing scored without PortWatch
    # "last" for April still uses March's Census figure, not February's.
    april = rows.filter((pl.col("month") == date(2019, 4, 1)) & (pl.col("model") == "last"))
    assert april["nowcast"][0] == pytest.approx(p.census[2])


def test_portwatch_models_recover_an_exact_ratio() -> None:
    census, pw = _inputs()
    rows = backtest(build_panel(census, pw, "all_oil_exports"), "all_oil_exports")
    med = dict(rows.group_by("model").agg(pl.col("error").median()).iter_rows())
    assert set(med) == set(MODELS)
    for m in ("ratio12", "ratio3", "change", "coasts"):
        assert med[m] < 1e-6, m
    assert med["last"] > 0.02 and med["avg3"] > 0.02


def test_choose_skips_baselines_and_uses_only_the_choice_years() -> None:
    def rows(model: str, year: int, error: float) -> dict[str, object]:
        return {"series": "s", "month": date(year, 1, 1), "model": model, "nowcast": 1.0,
                "actual": 1.0, "error": error}

    df = pl.DataFrame([rows("last", 2021, 0.0), rows("ratio3", 2021, 0.2),
                       rows("coasts", 2022, 0.1), rows("change", 2024, 0.0)])
    assert choose(df) == {"s": "coasts"}


def test_target_needs_every_year_and_a_quarter_less_overall() -> None:
    def rows(year: int, chosen: float, last: float) -> list[dict[str, object]]:
        base = {"series": "s", "month": date(year, 6, 1), "nowcast": 1.0, "actual": 1.0}
        return [{**base, "model": "ratio3", "error": chosen},
                {**base, "model": "last", "error": last},
                {**base, "model": "avg3", "error": last * 2}]

    good = pl.DataFrame([r for y in (2023, 2024) for r in rows(y, 0.02, 0.05)])
    table = scores(good, {"s": "ratio3"})
    assert target_met(table) == {"s": True}
    assert table.filter(pl.col("year") == "2023+")["vs_baseline"][0] == pytest.approx(0.4)
    # One bad year fails it, even though the total is still well ahead.
    bad = pl.DataFrame(rows(2023, 0.01, 0.05) + rows(2024, 0.06, 0.05) + rows(2025, 0.01, 0.05))
    assert target_met(scores(bad, {"s": "ratio3"})) == {"s": False}
