"""Step 4 of ML3: the frozen Suez-return forecast against what happened, for the dashboard.

The disruption warning job (``src.ml.disruption_warning.predict --site``) calls
``load_panel`` each week and puts the result in the page as ``D.route``.
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import polars as pl

from src.ml.route_shift.episodes import CAPE
from src.ml.route_shift.response import FORECASTS, TYPES, load_frozen, score

FROZEN = Path(__file__).with_name("forecast_2026-10-10.json")
#: First week shown, so the charts include the months before the return.
SINCE = date(2026, 6, 1)


def _round(v: float | None) -> float | None:
    return None if v is None else round(v, 2)


def panel(weekly: pl.DataFrame, frozen_path: Path = FROZEN) -> dict[str, Any]:
    """Weekly Cape transits per type beside the three frozen forecasts, and the scores."""
    doc = json.loads(frozen_path.read_text(encoding="utf-8"))
    frozen = load_frozen(frozen_path)
    actual = weekly.filter((pl.col("chokepoint") == CAPE) & pl.col("ship_type").is_in(TYPES)
                           & (pl.col("week_start") >= SINCE))
    weeks = sorted(set(actual["week_start"]) | set(frozen["week_start"]))
    out: dict[str, Any] = {}
    for st in TYPES:
        a = dict(actual.filter(pl.col("ship_type") == st)
                 .select("week_start", "transits").iter_rows())
        f = frozen.filter(pl.col("ship_type") == st)
        lines = {c: dict(f.select("week_start", c).iter_rows()) for c in FORECASTS}
        out[st] = {"actual": [_round(a.get(w)) for w in weeks],
                   **{c: [_round(lines[c].get(w)) for w in weeks] for c in FORECASTS}}
    scores = score(frozen, weekly)
    first = frozen["week_start"].min()
    assert isinstance(first, date)
    return {
        "frozen_on": doc["frozen_on"], "code": doc["code"], "data_through": doc["data_through"],
        "share": doc["share"], "first": first.isoformat(),
        "horizon": frozen["week_start"].n_unique(),
        "weeks": [w.isoformat() for w in weeks], "types": out,
        "scores": [{k: _round(v) if isinstance(v, float) else v for k, v in r.items()}
                   for r in scores.iter_rows(named=True)],
    }


def load_panel() -> dict[str, Any]:
    """The panel from HF's chokepoint transits."""
    from src.ml.route_shift.episodes import load_daily, weekly_transits

    return panel(weekly_transits(load_daily()))
