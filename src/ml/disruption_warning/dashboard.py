"""The public warning dashboard: one self-contained HTML page.

Built each week by ``predict.py --site``. Everything the page shows is in a
JSON block inside it; a little plain JavaScript draws the tables, map and
charts, so the page needs no server and no outside scripts.
"""
from __future__ import annotations

import html
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

import polars as pl

from src.ml.disruption_warning.evaluate import BUDGET
from src.ml.disruption_warning.predict import (
    ALTERNATIVE_KM,
    DRIVER_GROUPS,
    EVENT_LOOKBACK_DAYS,
    PROFILE_COLUMNS,
    rows_json,
)

if TYPE_CHECKING:
    from src.ml.disruption_warning.predict import Forecast

#: The track record shown: this many weeks of target weeks.
RECORD_WEEKS = 104
PAGE = Path(__file__).with_name("dashboard.html")
#: Coastlines for the map: an SVG path in the page's own projection
#: (Natural Earth 1:50m land, public domain; built by ``land_path.py``).
LAND = Path(__file__).with_name("land_path.txt")
RECENT_ORIGINS = 6
CALIBRATION_BINS = [0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.4, 1.0]
HORIZON_NAMES = {1: "Last week", 2: "This week", 3: "Next week"}
#: Past disruptions listed per port in the drill-down.
PAST_SHOWN = 6


def _num(v: Any, digits: int = 1) -> Any:
    return None if v is None or v != v else round(float(v), digits)


def port_details(fc: Forecast, record: pl.DataFrame) -> dict[str, Any]:
    """What the page shows when a port is clicked, keyed by port_id.

    ``weeks`` is shared; each port's ``calls``/``usual`` line up with it, and
    ``marks`` has one letter per week: D = disruption, S = seasonal drop."""
    out: dict[str, dict[str, Any]] = {}
    weeks: list[str] = []
    if not fc.calls.is_empty():
        wk = sorted(fc.calls["week_start"].unique().to_list())
        weeks = [w.isoformat() for w in wk]
        index = {w: i for i, w in enumerate(wk)}
        for (pid,), g in fc.calls.sort("week_start").group_by("port_id", maintain_order=True):
            calls: list[Any] = [None] * len(wk)
            usual: list[Any] = [None] * len(wk)
            marks = ["."] * len(wk)
            for r in g.iter_rows(named=True):
                i = index[r["week_start"]]
                calls[i], usual[i] = _num(r["y"], 0), _num(r["base"], 0)
                if r["is_disruption"]:
                    marks[i] = "D"
                elif r["is_seasonal"]:
                    marks[i] = "S"
            out[str(pid)] = {"calls": calls, "usual": usual, "marks": "".join(marks)}
    if not fc.past.is_empty():
        for (pid,), g in fc.past.sort("week_start", descending=True).group_by(
                "port_id", maintain_order=True):
            d = out.setdefault(str(pid), {})
            d["past_n"] = g.height
            d["past"] = [[r["week_start"].isoformat(), _num(r["drop_pct"], 3)]
                         for r in g.head(PAST_SHOWN).iter_rows(named=True)]
    if not record.is_empty():
        per_port = record.group_by("port_id").agg(
            pl.col("flagged").sum().alias("alerts"),
            (pl.col("flagged") & pl.col("happened")).sum().alias("hits"),
            pl.col("target_week").filter(pl.col("happened")).n_unique().alias("disruptions"),
            pl.col("target_week").filter(pl.col("happened") & pl.col("flagged")).n_unique()
            .alias("caught"),
        )
        for r in per_port.iter_rows(named=True):
            out.setdefault(str(r["port_id"]), {})["record"] = [
                r["alerts"], r["hits"], r["disruptions"], r["caught"]]
    if not fc.nearby_events.is_empty():
        for (pid,), g in fc.nearby_events.group_by("port_id", maintain_order=True):
            out.setdefault(str(pid), {})["events"] = rows_json(
                g.select("name", "type", "level", "from_date", "km"))
    scored = set(fc.warnings["port_id"].to_list()) if "port_id" in fc.warnings.columns else None
    if scored is not None:
        out = {k: v for k, v in out.items() if k in scored}
    first = record["target_week"].min() if not record.is_empty() else None
    return {"weeks": weeks, "ports": out,
            "record_first": first.isoformat() if isinstance(first, date) else None}


def _record(record: pl.DataFrame) -> dict[str, Any]:
    if record.is_empty():
        return {"weeks": [], "recent": [], "calibration": [], "summary": None}
    last = record["target_week"].max()
    assert isinstance(last, date)
    recent = record.filter(pl.col("target_week") > last - timedelta(weeks=RECORD_WEEKS))
    weeks = (
        recent.group_by("target_week")
        .agg(
            pl.col("flagged").sum().alias("alerts"),
            (pl.col("flagged") & pl.col("happened")).sum().alias("hits"),
            # One target week is scored from three origins; count its
            # disruptions once, and "caught" if any horizon flagged it.
            pl.struct("port_id", "happened").filter(pl.col("happened")).n_unique()
            .alias("disruptions"),
            pl.col("port_id").filter(pl.col("happened") & pl.col("flagged")).n_unique()
            .alias("caught"),
        )
        .sort("target_week")
    )
    flagged = recent.filter(pl.col("flagged"))
    per_row = recent.select(pl.col("happened").mean()).item()
    summary = {
        "weeks": recent["target_week"].n_unique(),
        "alerts": flagged.height,
        "hits": int(flagged["happened"].sum()),
        "precision": flagged["happened"].mean() if flagged.height else None,
        "base_rate": per_row,
        "caught": int(weeks["caught"].sum()),
        "disruptions": int(weeks["disruptions"].sum()),
        "first": recent["target_week"].min(),
        "last": last,
    }
    labels = [f"{a:.0%}-{b:.0%}" for a, b in zip(CALIBRATION_BINS, CALIBRATION_BINS[1:],
                                                   strict=False)]
    calib = (
        record.with_columns(
            pl.col("chance").cut(CALIBRATION_BINS[1:-1], labels=labels, left_closed=True)
            .alias("bin")
        )
        .group_by("bin")
        .agg(pl.len().alias("n"), pl.col("chance").mean().alias("said"),
             pl.col("happened").mean().alias("happened"))
        .sort("bin")
        .with_columns(pl.col("bin").cast(pl.Utf8))
    )
    origins = sorted(record["origin_week"].unique().to_list())[-RECENT_ORIGINS:]
    recent_alerts = (
        record.filter(pl.col("flagged") & pl.col("origin_week").is_in(origins))
        .sort("target_week", "chance", descending=[True, True])
        .unique(["target_week", "port_id"], keep="first", maintain_order=True)
    )
    return {
        "weeks": rows_json(weeks),
        "recent": rows_json(recent_alerts.select(
            "target_week", "horizon", "port_id", "chance", "happened", "drop_pct")),
        "calibration": rows_json(calib),
        "summary": {k: (v.isoformat() if isinstance(v, date) else v) for k, v in summary.items()},
    }


def payload(fc: Forecast, record: pl.DataFrame, history: pl.DataFrame) -> dict[str, Any]:
    w = fc.warnings
    extra = [c for c in PROFILE_COLUMNS
             if c in w.columns and c not in ("latitude", "longitude")]
    ports = w.filter(pl.col("horizon") == 2).select(
        "port_id", *extra,
        pl.col("latitude", "longitude").round(2), pl.col("normal_calls").round(1),
        "last_calls", pl.col("last_drop_pct").round(3),
    )
    names = dict(zip(ports["port_id"], ports["port_name"], strict=True))
    countries = dict(zip(ports["port_id"], ports["country"], strict=True))
    rec = _record(record)
    for r in rec["recent"]:
        r["port_name"], r["country"] = names.get(r["port_id"]), countries.get(r["port_id"])
    regions = (
        w.group_by("horizon", "continent")
        .agg(pl.col("chance").sum().alias("expected"), pl.len().alias("ports"),
             pl.col("flagged").sum().alias("flagged"))
        .sort("horizon", "expected", descending=[False, True])
    )
    return {
        "origin": fc.origin.isoformat(),
        "release": fc.release.isoformat(),
        "generated": datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "budget": BUDGET,
        "alt_km": ALTERNATIVE_KM,
        "horizons": {
            str(h): {"name": HORIZON_NAMES[h],
                     "week": (fc.origin + timedelta(weeks=h)).isoformat()}
            for h in (1, 2, 3)
        },
        "ports": rows_json(ports),
        "chances": {
            str(h): rows_json(
                w.filter(pl.col("horizon") == h).select(
                    "port_id", pl.col("chance").round(4), pl.col("lift").round(1), "flagged",
                    "reasons", "alternatives",
                    *(["drivers"] if "drivers" in w.columns else []),
                )
            )
            for h in (1, 2, 3)
        },
        "regions": rows_json(regions),
        "record": rec,
        "driver_groups": DRIVER_GROUPS,
        "event_days": EVENT_LOOKBACK_DAYS,
        "detail": port_details(fc, record),
        "history_rows": history.height,
        "model": {"rounds": fc.rounds, "trained_rows": fc.trained_rows},
    }


def render(fc: Forecast, record: pl.DataFrame, history: pl.DataFrame) -> str:
    data = json.dumps(payload(fc, record, history), separators=(",", ":"), default=str)
    # A "</" inside the JSON would end the script block early.
    data = data.replace("</", "<\\/")
    page = PAGE.read_text(encoding="utf-8")
    land = LAND.read_text(encoding="utf-8").strip() if LAND.exists() else ""
    return (page.replace("__TITLE__", html.escape(TITLE)).replace("__LAND__", land)
            .replace("__DATA__", data))


TITLE = "Port Disruption Watch"
