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
from src.ml.disruption_warning.predict import ALTERNATIVE_KM, rows_json

if TYPE_CHECKING:
    from src.ml.disruption_warning.predict import Forecast

#: The track record shown: this many weeks of target weeks.
RECORD_WEEKS = 104
PAGE = Path(__file__).with_name("dashboard.html")
RECENT_ORIGINS = 6
CALIBRATION_BINS = [0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.4, 1.0]
HORIZON_NAMES = {1: "Last week", 2: "This week", 3: "Next week"}


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
    ports = w.filter(pl.col("horizon") == 2).select(
        "port_id", "port_name", "country", "continent",
        pl.col("latitude", "longitude").round(2), pl.col("normal_calls").round(1),
        "last_calls", pl.col("last_drop_pct").round(3), "industry_top1",
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
                )
            )
            for h in (1, 2, 3)
        },
        "regions": rows_json(regions),
        "record": rec,
        "history_rows": history.height,
        "model": {"rounds": fc.rounds, "trained_rows": fc.trained_rows},
    }


def render(fc: Forecast, record: pl.DataFrame, history: pl.DataFrame) -> str:
    data = json.dumps(payload(fc, record, history), separators=(",", ":"), default=str)
    # A "</" inside the JSON would end the script block early.
    data = data.replace("</", "<\\/")
    page = PAGE.read_text(encoding="utf-8")
    return page.replace("__TITLE__", html.escape(TITLE)).replace("__DATA__", data)


TITLE = "Port Disruption Watch"
