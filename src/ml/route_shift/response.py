"""Step 3 of ML3: forecast Cape of Good Hope transits as ships return to Suez.

The model is one hypothesis taken from the 2023 switch (``episodes``): the return
mirrors it. Container lines moved first and tankers and bulk carriers followed
over 1–3 months, so if a share ``s`` of the diverted container traffic has come
back, tankers and bulk will bring back the same share along their 2023 lag curves,
counted from the return's start. By the time the forecast is made they may be
behind that path (they hadn't moved by 2026-10-04); the model says they catch up
to it. Nothing in it predicts *more* container ships returning: their share is
held where it is, because that depends on politics.

Every forecast starts from the newest 3 full weeks (the "no change" level) and,
for ship types whose Cape counts are seasonal (step 1's choice), adds the usual
movement from those weeks to each target week, averaged over the years whose
weeks no Cape episode touched. Two baselines get the same treatment:

* no change: the level stays put;
* all back at once: every ship still diverted returns now, 1 for 1, so the Cape
  loses the rest of its 2023 gain at once.

Run ``python -m src.ml.route_shift.response --freeze FILE`` to write the forecast,
``--score FILE`` to score a frozen one against the newest data (both read HF).
"""
from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from src.ml.route_shift.episodes import (
    AFTER_WEEKS,
    CAPE,
    EPISODES,
    SUEZ,
    YEAR,
    Episode,
    Series,
    build_series,
    changes,
    markdown,
    measure,
)

TYPES = ("container", "tanker", "dry_bulk")
FOLLOWERS = ("tanker", "dry_bulk")
HORIZON = 8
RECENT = 3  # full weeks averaged for the "no change" level

_BY_NAME = {e.name: e for e in EPISODES}
ONSET: Episode = _BY_NAME["red_sea"]
RETURN: Episode = _BY_NAME["suez_return"]


def lag_curve(path: np.ndarray, settled: float) -> np.ndarray:
    """Share of the lasting change reached by each week: 3-week average, never falling."""
    known = ~np.isnan(path)
    filled = np.where(known, path, 0.0)
    num = np.convolve(filled, np.ones(3), "same")
    den = np.convolve(known.astype(float), np.ones(3), "same")
    share = np.where(den > 0, num / np.maximum(den, 1) / settled, 0.0)
    return np.clip(np.maximum.accumulate(np.clip(share, 0, None)), 0, 1)


@dataclass
class Fit:
    gain: dict[str, float]  # Cape's lasting gain in the 2023 switch, per type
    curve: dict[str, list[float]]  # lag curve per type, weeks 0..AFTER_WEEKS
    seasonal: dict[str, bool]
    share: float  # share of diverted container traffic back so far
    share_parts: dict[str, float]
    now: dict[str, float]  # Cape change since the return started, per type


def fit(weekly: pl.DataFrame) -> Fit:
    measured = measure(weekly)
    series = build_series(weekly)

    def row(episode: str, cp: str, st: str) -> dict[str, Any]:
        return measured.filter((pl.col("episode") == episode) & (pl.col("chokepoint") == cp)
                               & (pl.col("ship_type") == st)).row(0, named=True)

    gain, curve, seasonal, now = {}, {}, {}, {}
    offsets = np.arange(AFTER_WEEKS + 1)
    for st in TYPES:
        r = row(ONSET.name, CAPE, st)
        s = series[(CAPE, st)]
        seasonal[st] = r["method"] == "seasonal"
        path = changes(s, np.array([s.index(ONSET.start)]), offsets, seasonal[st])[0]
        gain[st] = r["change"]
        now[st] = row(RETURN.name, CAPE, st)["change"]
        curve[st] = [round(float(v), 3) for v in lag_curve(path, r["change"])]
    # Share back: Suez's regained container transits over its 2023 loss, and the
    # Cape's lost ones over its 2023 gain, averaged.
    parts = {
        "suez": row(RETURN.name, SUEZ, "container")["change"]
        / -row(ONSET.name, SUEZ, "container")["change"],
        "cape": -row(RETURN.name, CAPE, "container")["change"] / gain["container"],
    }
    share = float(np.clip(np.mean(list(parts.values())), 0, 1))
    return Fit(gain, curve, seasonal, share, parts, now)


def _season(s: Series, last: int, target: np.ndarray) -> tuple[np.ndarray, list[int]]:
    """Usual change from the recent weeks to each target week, and the years used.

    Averaged over earlier years whose weeks no Cape episode touched.
    """
    spans = [e.affected() for e in EPISODES if CAPE in e.chokepoints()]
    rows, years = [], []
    for n in range(1, last // YEAR + 1):
        lo, hi = last - RECENT + 1 - n * YEAR, int(target[-1]) - n * YEAR
        if lo < 0 or any(not (s.weeks[hi] < a or s.weeks[lo] > b) for a, b in spans):
            continue
        recent = float(np.nanmean(s.x[lo:lo + RECENT]))
        rows.append(s.take(target - n * YEAR) - recent)
        years.append(s.weeks[lo].year)
    if not rows:
        return np.zeros(target.size), []
    return np.nanmean(np.array(rows), axis=0), years


def forecast(weekly: pl.DataFrame, f: Fit) -> pl.DataFrame:
    """Cape transits per type for the HORIZON weeks after the newest full week."""
    series = build_series(weekly)
    rows = []
    for st in TYPES:
        s = series[(CAPE, st)]
        last = int(np.flatnonzero(~np.isnan(s.x)).max())
        level = float(np.nanmean(s.x[last - RECENT + 1:last + 1]))
        target = np.arange(last + 1, last + 1 + HORIZON)
        season = _season(s, last, target)[0] if f.seasonal[st] else np.zeros(HORIZON)
        k_now = last - s.index(RETURN.start)
        curve = np.array(f.curve[st])
        g = curve[np.minimum(np.arange(k_now + 1, k_now + 1 + HORIZON), curve.size - 1)]
        # Followers reach the 2023 path for share s, from wherever they are now.
        mirror = (-f.share * f.gain[st] * g - f.now[st] if st in FOLLOWERS
                  else np.zeros(HORIZON))
        # All back at once: the Cape loses what's left of its 2023 gain.
        rest = -(f.gain[st] + f.now[st])
        for j, i in enumerate(target):
            rows.append({
                "week_start": s.weeks[last] + timedelta(weeks=j + 1), "ship_type": st,
                "weeks_since_return": k_now + j + 1,
                "no_change": level + season[j],
                "model": level + season[j] + mirror[j],
                "all_back": level + season[j] + rest,
            })
    return pl.DataFrame(rows)


FORECASTS = ("model", "no_change", "all_back")


def score(frozen: pl.DataFrame, weekly: pl.DataFrame) -> pl.DataFrame:
    """Mean absolute error (daily transits) per forecast, by type and overall."""
    actual = weekly.filter(pl.col("chokepoint") == CAPE).select(
        "week_start", "ship_type", pl.col("transits").alias("actual"))
    joined = frozen.join(actual, on=["week_start", "ship_type"])
    errs = [(pl.col(c) - pl.col("actual")).abs().mean().alias(c) for c in FORECASTS]
    by_type = joined.group_by("ship_type").agg(pl.len().alias("weeks"), *errs)
    overall = joined.select(pl.lit("all").alias("ship_type"), pl.len().alias("weeks"), *errs)
    return pl.concat([by_type.sort("ship_type"), overall])


def _git_head() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True,
                              text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def freeze(weekly: pl.DataFrame, path: Path) -> dict[str, Any]:
    f = fit(weekly)
    fc = forecast(weekly, f)
    doc = {
        "frozen_on": date.today().isoformat(), "code": _git_head(),
        "data_through": str(weekly.filter(pl.col("chokepoint") == CAPE)["week_start"].max()),
        "return_start": RETURN.start.isoformat(),
        "share": round(f.share, 3),
        "share_parts": {k: round(v, 3) for k, v in f.share_parts.items()},
        "gain": {k: round(v, 2) for k, v in f.gain.items()},
        "cape_change_so_far": {k: round(v, 2) for k, v in f.now.items()},
        "seasonal": f.seasonal, "curve": f.curve,
        "forecast": [{**r, "week_start": r["week_start"].isoformat(),
                      **{c: round(r[c], 2) for c in FORECASTS}} for r in fc.iter_rows(named=True)],
    }
    path.write_text(json.dumps(doc, indent=1) + "\n", encoding="utf-8")
    return doc


def load_frozen(path: Path) -> pl.DataFrame:
    doc = json.loads(path.read_text(encoding="utf-8"))
    return pl.DataFrame(doc["forecast"]).with_columns(pl.col("week_start").str.to_date())


def main(argv: list[str] | None = None) -> None:
    from src.ml.route_shift.episodes import load_daily, weekly_transits

    parser = argparse.ArgumentParser(description="Forecast Cape transits as ships return to Suez")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--freeze", type=Path, help="write the forecast to this JSON file")
    group.add_argument("--score", type=Path, help="score this frozen forecast")
    args = parser.parse_args(argv)

    weekly = weekly_transits(load_daily())
    if args.freeze:
        doc = freeze(weekly, args.freeze)
        print(json.dumps({k: v for k, v in doc.items() if k not in ("forecast", "curve")},
                         indent=1))
        print(markdown(load_frozen(args.freeze), ["week_start", "ship_type", *FORECASTS]))
    else:
        scores = score(load_frozen(args.score), weekly)
        print(markdown(scores, ["ship_type", "weeks", *FORECASTS]))


if __name__ == "__main__":
    main()
