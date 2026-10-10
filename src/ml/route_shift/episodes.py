"""Step 1 of ML3: how much traffic each route disruption moved, and how fast.

For each episode in ``EPISODES`` and each chokepoint involved, the change in daily
transits by ship type is measured week by week against the 8 weeks before the
start (leaving out the 2 weeks just before, when early movers had already
switched). Optionally the same weeks a year earlier are subtracted to remove the
season ("seasonal" method).

The noise level comes from placebo starts: the same measurement repeated at every
Monday on which none of that chokepoint's episodes could have affected the
weeks used. Each chokepoint and ship type uses whichever method (raw or seasonal)
is less noisy in those placebos, so the choice never looks at the episode itself.

Run: ``python -m src.ml.route_shift.episodes [--csv DIR]`` (reads HF).
"""
from __future__ import annotations

import argparse
import math
import warnings
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import polars as pl

SHIP_TYPES = ("container", "tanker", "dry_bulk", "general_cargo", "roro", "total")
#: Weeks in the baseline, and weeks between it and the start.
PRE_WEEKS = 8
GAP_WEEKS = 2
AFTER_WEEKS = 26
#: Weeks after the start averaged for the lasting change.
SETTLED = range(13, AFTER_WEEKS + 1)
#: An episode that hasn't reached SETTLED yet uses its newest weeks instead.
LATEST_WEEKS = 3
YEAR = 52
#: A change is real if it is at least this many noise levels.
SIGNIFICANT = 2.0
#: Weeks before an episode's start that it may already affect.
LEAD_WEEKS = 4


@dataclass(frozen=True)
class Episode:
    name: str
    label: str
    start: date  # Monday of the first affected week
    route: tuple[str, ...]  # where traffic changed first; route[0] is used for transfers
    alternatives: tuple[str, ...] = ()
    #: When the level stopped changing; None = settled AFTER_WEEKS after the start.
    end: date | None = None
    note: str = ""

    def affected(self) -> tuple[date, date]:
        last = self.end or self.start
        return self.start - timedelta(weeks=LEAD_WEEKS), last + timedelta(weeks=AFTER_WEEKS)

    def chokepoints(self) -> tuple[str, ...]:
        return self.route + self.alternatives


SUEZ, BAB, CAPE = "Suez Canal", "Bab el-Mandeb Strait", "Cape of Good Hope"

EPISODES: tuple[Episode, ...] = (
    Episode("ever_given", "Ever Given blocks Suez", date(2021, 3, 22), (SUEZ,), (CAPE,),
            end=date(2021, 4, 5), note="6 days; too short for ships to reroute"),
    Episode("panama_drought", "Panama Canal drought limits", date(2023, 10, 30),
            ("Panama Canal",), ("Magellan Strait",), end=date(2024, 8, 26),
            note="slot cuts from Nov 2023; most diverted ships went via Suez or the Cape, "
                 "which the Red Sea crisis confounds 7 weeks later"),
    Episode("red_sea", "Red Sea attacks", date(2023, 12, 18), (SUEZ, BAB), (CAPE,),
            note="the main case; still on"),
    Episode("hormuz", "Hormuz closure", date(2026, 3, 2), ("Strait of Hormuz",),
            end=date(2026, 7, 13),
            note="closed 02-28; no sea route around it; reopened 06-18, collapsed 07-08"),
    Episode("bab_2026", "Bab el-Mandeb second drop", date(2026, 7, 20), (BAB,), (CAPE,),
            note="after the Houthi attacks on Yanbu; mostly Red Sea port traffic"),
    Episode("suez_return", "Ships return to Suez", date(2026, 8, 24), (SUEZ, BAB), (CAPE,),
            note="live; container ships first"),
)


# ── Data ──────────────────────────────────────────────────────────────────


def weekly_transits(daily: pl.DataFrame) -> pl.DataFrame:
    """Mean daily transits per Monday-to-Sunday week, by chokepoint and ship type.

    ``daily`` has ``transit_date, chokepoint_name`` and ``n_<type>`` columns. Weeks
    with fewer than 7 days are dropped. Long format:
    ``week_start, chokepoint, ship_type, transits``.
    """
    cols = [f"n_{t}" for t in SHIP_TYPES]
    return (
        daily.unique(subset=["chokepoint_name", "transit_date"], keep="last")
        .with_columns(pl.col("transit_date").dt.truncate("1w").alias("week_start"))
        .group_by("chokepoint_name", "week_start")
        .agg(pl.len().alias("days"), *[pl.col(c).cast(pl.Float64).mean() for c in cols])
        .filter(pl.col("days") == 7)
        .drop("days")
        .rename({"chokepoint_name": "chokepoint"})
        .unpivot(index=["chokepoint", "week_start"], on=cols, variable_name="ship_type",
                 value_name="transits")
        .with_columns(pl.col("ship_type").str.strip_prefix("n_"))
        .sort("chokepoint", "ship_type", "week_start")
    )


def load_daily() -> pl.DataFrame:
    """PortWatch daily chokepoint transits from HF, newest ingest last."""
    from src.ml.port_forecast.backtest import hf_connection, hf_table

    cols = ", ".join(f"n_{t}" for t in SHIP_TYPES)
    return hf_connection().execute(
        f"""SELECT transit_date, chokepoint_name, {cols}
            FROM read_parquet({hf_table('chokepoint_transits')})
            WHERE source = 'imf_portwatch' ORDER BY ingested_at"""
    ).pl()


# ── Measurement ───────────────────────────────────────────────────────────


@dataclass
class Series:
    """One chokepoint and ship type on a gap-free weekly grid (missing = NaN)."""

    weeks: list[date]
    x: np.ndarray

    def index(self, d: date) -> int:
        return (d - self.weeks[0]).days // 7

    def take(self, idx: np.ndarray) -> np.ndarray:
        ok = (idx >= 0) & (idx < self.x.size)
        out = np.full(idx.shape, np.nan)
        out[ok] = self.x[idx[ok]]
        return out


def build_series(weekly: pl.DataFrame) -> dict[tuple[str, str], Series]:
    first, last = weekly["week_start"].min(), weekly["week_start"].max()
    assert isinstance(first, date) and isinstance(last, date)
    weeks = [first + timedelta(weeks=k) for k in range((last - first).days // 7 + 1)]
    out = {}
    for (cp, st), grp in weekly.group_by("chokepoint", "ship_type"):
        x = np.full(len(weeks), np.nan)
        idx = ((grp["week_start"] - first).dt.total_days() // 7).to_numpy()
        x[idx] = grp["transits"].to_numpy()
        out[(str(cp), str(st))] = Series(weeks, x)
    return out


def _baseline(s: Series, i: np.ndarray) -> np.ndarray:
    """Mean of the PRE_WEEKS weeks ending GAP_WEEKS before each start index."""
    offsets = np.arange(-GAP_WEEKS - PRE_WEEKS, -GAP_WEEKS)
    vals = s.take(i[:, None] + offsets[None, :])
    enough = np.sum(~np.isnan(vals), axis=1) >= PRE_WEEKS // 2
    return np.where(enough, _nanmean(vals), np.nan)


def changes(s: Series, starts: np.ndarray, offsets: np.ndarray, seasonal: bool) -> np.ndarray:
    """Change vs baseline at each offset, shape (len(starts), len(offsets))."""
    out = s.take(starts[:, None] + offsets[None, :]) - _baseline(s, starts)[:, None]
    if seasonal:
        prior = starts - YEAR
        out -= s.take(prior[:, None] + offsets[None, :]) - _baseline(s, prior)[:, None]
    return out


def _mean_over(c: np.ndarray) -> np.ndarray:
    """Mean across offsets, NaN unless at least half are known."""
    enough = np.sum(~np.isnan(c), axis=1) >= max(1, c.shape[1] // 2)
    return np.where(enough, _nanmean(c), np.nan)


def _nanmean(a: np.ndarray) -> np.ndarray:
    """Row means ignoring NaN; an all-NaN row gives NaN without a warning."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        return np.nanmean(a, axis=1)


def placebo_starts(s: Series, chokepoint: str, episodes: Sequence[Episode],
                   seasonal: bool) -> np.ndarray:
    """Start indexes whose measured weeks no episode at this chokepoint touches."""
    back = GAP_WEEKS + PRE_WEEKS + (YEAR if seasonal else 0)
    blocked = [e.affected() for e in episodes if chokepoint in e.chokepoints()]
    keep = []
    for i in range(back, s.x.size - AFTER_WEEKS):
        lo, hi = s.weeks[i - back], s.weeks[i + AFTER_WEEKS]
        if all(hi < a or lo > b for a, b in blocked):
            keep.append(i)
    return np.array(keep, dtype=int)


def noise(s: Series, starts: np.ndarray, offsets: np.ndarray, seasonal: bool) -> float:
    """Root-mean-square of the averaged change over placebo starts (true change 0)."""
    if starts.size == 0:
        return math.nan
    m = _mean_over(changes(s, starts, offsets, seasonal))
    m = m[~np.isnan(m)]
    return float(np.sqrt(np.mean(m**2))) if m.size else math.nan


def _weeks_to(path: np.ndarray, target: float, frac: float) -> int | None:
    """First week the 3-week average change reaches frac of target."""
    known = ~np.isnan(path)
    if not known.any():
        return None
    filled = np.where(known, path, 0.0)
    num = np.convolve(filled, np.ones(3), "same")
    den = np.convolve(known.astype(float), np.ones(3), "same")
    smooth = np.where(den > 0, num / np.maximum(den, 1), np.nan)
    hit = np.flatnonzero(np.sign(target) * smooth >= frac * abs(target))
    return int(hit[0]) if hit.size else None


def measure(weekly: pl.DataFrame, episodes: Sequence[Episode] = EPISODES) -> pl.DataFrame:
    """One row per episode, chokepoint and ship type: the change, its noise, its timing."""
    series = build_series(weekly)
    offsets = np.arange(AFTER_WEEKS + 1)
    rows = []
    for (cp, st), s in sorted(series.items()):
        placebos = {m: placebo_starts(s, cp, episodes, m) for m in (False, True)}
        settled = np.array(SETTLED)
        method = min((False, True), key=lambda m: _nan_last(noise(s, placebos[m], settled, m)))
        for e in episodes:
            if cp not in e.chokepoints():
                continue
            i = s.index(e.start)
            path = changes(s, np.array([i]), offsets, method)[0]
            seen = int(np.flatnonzero(~np.isnan(path)).max(initial=-1)) + 1
            weeks = settled if seen > settled[0] else np.arange(max(0, seen - LATEST_WEEKS), seen)
            change = float(_mean_over(path[weeks][None, :])[0]) if weeks.size else math.nan
            sd = noise(s, placebos[method], weeks, method) if weeks.size else math.nan
            z = change / sd if sd > 0 else math.nan
            real = abs(z) >= SIGNIFICANT
            rows.append({
                "episode": e.name, "chokepoint": cp,
                "role": "route" if cp in e.route else "alternative",
                "ship_type": st, "weeks_seen": seen, "settled": seen > settled[0],
                "before": float(_baseline(s, np.array([i]))[0]),
                "change": change, "noise": sd, "z": z,
                "method": "seasonal" if method else "raw",
                "placebos": int(placebos[method].size),
                "half_weeks": _weeks_to(path, change, 0.5) if real else None,
                "most_weeks": _weeks_to(path, change, 0.9) if real else None,
            })
    return pl.DataFrame(rows, infer_schema_length=None)


def _nan_last(v: float) -> float:
    return math.inf if math.isnan(v) else v


def transfers(measured: pl.DataFrame, episodes: Sequence[Episode] = EPISODES) -> pl.DataFrame:
    """Alternative-route gain per ship lost from route[0], by ship type, with its noise."""
    rows = []
    for e in episodes:
        if not e.alternatives:
            continue
        m = measured.filter(pl.col("episode") == e.name)
        for st in SHIP_TYPES:
            lost = m.filter((pl.col("chokepoint") == e.route[0]) & (pl.col("ship_type") == st))
            alt = m.filter(pl.col("chokepoint").is_in(e.alternatives) & (pl.col("ship_type") == st))
            if lost.is_empty() or alt.is_empty():
                continue
            a, b = alt["change"].sum(), lost["change"][0]
            na = math.sqrt(sum(v**2 for v in alt["noise"]))
            nb = lost["noise"][0]
            real = abs(b) >= SIGNIFICANT * nb
            ratio = -a / b if real else math.nan
            rows.append({
                "episode": e.name, "ship_type": st, "route_change": b, "alt_change": a,
                "ratio": ratio,
                "ratio_noise": math.sqrt(na**2 + (ratio * nb) ** 2) / abs(b) if real else math.nan,
            })
    return pl.DataFrame(rows, infer_schema_length=None)


# ── CLI ───────────────────────────────────────────────────────────────────


def _fmt(v: object) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    if isinstance(v, float):
        return f"{v:+.1f}" if abs(v) < 1000 else f"{v:+.0f}"
    return str(v)


def markdown(df: pl.DataFrame, cols: Sequence[str]) -> str:
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(_fmt(r[c]) for c in cols) + " |" for r in df.iter_rows(named=True)]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Measure traffic shifts in each route episode")
    parser.add_argument("--csv", type=Path, help="also write episodes.csv and transfers.csv here")
    args = parser.parse_args(argv)

    weekly = weekly_transits(load_daily())
    measured = measure(weekly)
    moved = transfers(measured)
    shown = measured.filter(pl.col("ship_type").is_in(["container", "tanker", "dry_bulk", "total"]))
    print(markdown(shown, ["episode", "chokepoint", "ship_type", "weeks_seen", "before", "change",
                           "noise", "z", "method", "half_weeks", "most_weeks"]))
    print()
    print(markdown(moved, ["episode", "ship_type", "route_change", "alt_change", "ratio",
                           "ratio_noise"]))
    if args.csv:
        args.csv.mkdir(parents=True, exist_ok=True)
        measured.write_csv(args.csv / "episodes.csv")
        moved.write_csv(args.csv / "transfers.csv")


if __name__ == "__main__":
    main()
