"""Weekly live warning: train on everything known, score the newest week.

    python -m src.ml.disruption_warning.predict --hf --site site/
    python -m src.ml.disruption_warning.predict --hf --seed-history backtest.parquet

The newest origin is the last week with published port data. The model is
trained on every row whose target week is on or before it (the backtest's
rule at a cutoff), then scores that origin's three horizons for every port.

Each run appends its warnings to ``history.parquet`` in the HF dataset
``HISTORY_REPO`` (this job is its only writer), so later runs can show what
the model said and what then happened. The history is seeded once from the
backtest's out-of-sample rows (``--seed-history``), never from a model that
had seen those weeks.

``--site DIR`` writes the dashboard (``dashboard.py``) to ``DIR/index.html``;
``--space`` also uploads it to the HF Space ``SPACE_REPO``. CI deploys the
same folder to GitHub Pages (``.github/workflows/warning.yml``).
"""
from __future__ import annotations

import argparse
import io
import math
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from src.ml.disruption_warning.baselines import RELEASE_LAG_DAYS, _km, weekly_origins
from src.ml.disruption_warning.evaluate import BUDGET
from src.ml.disruption_warning.features import (
    CHOKEPOINTS,
    DROP_STATE,
    EVENTS,
    FEATURES,
    REGION,
    build_features,
    load_inputs,
)
from src.ml.disruption_warning.labels import label_drops
from src.ml.disruption_warning.train import (
    MIN_CALIBRATION_POSITIVES,
    MODEL,
    TrainConfig,
    apply_platt,
    fit_model,
    fit_platt,
)
from src.ml.port_forecast.baselines import weekly_grid

HISTORY_REPO = "ZanderL1337/port-disruption-warnings"
HISTORY_FILE = "history.parquet"
SPACE_REPO = "ZanderL1337/port-disruption-watch"
SPACE_README = """---
title: Port Disruption Watch
emoji: 🚢
colorFrom: blue
colorTo: gray
sdk: static
pinned: false
license: mit
short_description: Weekly early warning of sudden drops in port calls
---

Built weekly by
[ShippingDataPipeline](https://github.com/Zanderl1987/ShippingDataPipeline)
from IMF PortWatch data. The same page is on GitHub Pages.
"""
#: Alternatives offered for a flagged port: this close, and at most this likely to drop.
ALTERNATIVE_KM = 500.0
ALTERNATIVE_MAX_CHANCE = 0.02

#: Plain-language names for groups of inputs, used to say why a port is flagged.
REASONS: dict[str, list[str]] = {
    "calls already falling": [
        "origin_z", "z_lag_1", "z_lag_2", "min_z_4", "origin_drop_pct", "origin_drop",
        "lag_0", "lag_1", "lag_2", "lag_3", "trend_4_vs_13", "change_vs_year_ago",
    ],
    "drops often at this port": [
        "weeks_since_drop", "drops_52", "disruptions_52", "base_rate", "noise_ratio", "cv_13",
        "mean_4", "mean_8", "mean_13", "mean_52",
    ],
    "time of year / holidays": [
        "target_week_of_year", "weeks_from_chinese_new_year", "weeks_from_eid_al_fitr",
        "weeks_from_eid_al_adha", "last_year", "last_year_vs_its_mean_4", "seasonal_avg",
        "dropped_near_target_last_year", "world_drop_share",
    ],
    "storm or disaster alert nearby": EVENTS,
    "chokepoint traffic shift": CHOKEPOINTS,
    "nearby ports dropping": [f for f in REGION if f != "world_drop_share"],
}
_OTHER = "port type and location"
_GROUPED = {f for fs in REASONS.values() for f in fs}
assert _GROUPED <= set(FEATURES) and set(DROP_STATE) <= _GROUPED


@dataclass
class Forecast:
    origin: date
    release: date
    warnings: pl.DataFrame
    rounds: int
    trained_rows: int


def live_origin(labels: pl.DataFrame) -> date:
    """The newest week with a published label: the release's last full week."""
    last = labels.filter(pl.col("is_disruption").is_not_null())["week_start"].max()
    if not isinstance(last, date):
        raise ValueError("no labelled weeks")
    return last


def reasons(contrib: Any, top: int = 2) -> list[list[str]]:
    """Per row, the input groups that pushed its chance up the most."""
    names = FEATURES
    groups = {g: [names.index(f) for f in fs] for g, fs in REASONS.items()}
    groups[_OTHER] = [i for i, f in enumerate(names) if f not in _GROUPED]
    sums = {g: contrib[:, idx].sum(axis=1) for g, idx in groups.items()}
    out = []
    for r in range(contrib.shape[0]):
        ranked = sorted(((v[r], g) for g, v in sums.items()), reverse=True)
        out.append([g for v, g in ranked[:top] if v > 0.05])
    return out


def flag_top(rows: pl.DataFrame, score: str = "chance") -> pl.Expr:
    """True for the top ``BUDGET`` share of ports per origin and horizon."""
    k = (pl.len().over("origin_week", "horizon") * BUDGET).ceil().clip(lower_bound=1)
    rank = pl.col(score).rank("ordinal", descending=True).over("origin_week", "horizon")
    return rank <= k


def alternatives(warn: pl.DataFrame) -> pl.DataFrame:
    """For each flagged port-horizon, the nearest ports within
    ``ALTERNATIVE_KM`` that are unlikely to drop that week."""
    flagged = warn.filter(pl.col("flagged")).select("horizon", "port_id", "latitude", "longitude")
    calm = warn.filter(pl.col("chance") <= ALTERNATIVE_MAX_CHANCE).select(
        "horizon", pl.col("port_id").alias("alt_id"), pl.col("port_name").alias("alt_name"),
        pl.col("latitude").alias("alt_lat"), pl.col("longitude").alias("alt_lon"),
        pl.col("normal_calls").alias("alt_calls"),
    )
    pairs = flagged.join(calm, on="horizon").with_columns(
        _km(pl.col("latitude"), pl.col("longitude"), pl.col("alt_lat"), pl.col("alt_lon"))
        .alias("km")
    )
    return (
        pairs.filter((pl.col("km") <= ALTERNATIVE_KM) & (pl.col("alt_id") != pl.col("port_id")))
        .sort("km")
        .group_by("horizon", "port_id", maintain_order=True)
        .agg(pl.struct(name="alt_name", km=pl.col("km").round(0), calls=pl.col("alt_calls"))
             .head(3).alias("alternatives"))
    )


def forecast(
    weekly: pl.DataFrame,
    events: pl.DataFrame,
    profiles: pl.DataFrame,
    chokepoints: pl.DataFrame,
    config: TrainConfig | None = None,
    history: pl.DataFrame | None = None,
) -> Forecast:
    """With ``config.calibrate == "history"`` and a ``history`` holding enough
    known outcomes, the chances are recalibrated on the last
    ``config.calibration_weeks`` of past warnings (as the backtest does)."""
    config = config or TrainConfig()
    labels = label_drops(weekly_grid(weekly))
    origin = live_origin(labels)
    train = build_features(
        weekly, events, profiles, chokepoints, weekly_origins(labels, config.train_start)
    )
    known = train.filter(pl.col("target_week") <= origin)
    fitted = fit_model(known, origin, config)
    live = build_features(weekly, events, profiles, chokepoints, [origin], labelled_only=False)
    # The ports the backtest scored: big enough to label (a label at the origin).
    eligible = labels.filter(
        (pl.col("week_start") == origin) & pl.col("is_disruption").is_not_null()
    ).select("port_id")
    live = live.join(eligible, on="port_id", how="semi")
    chance = fitted.predict(live)
    if config.calibrate == "history" and history is not None:
        past = track_record(history, labels).filter(
            pl.col("target_week") > origin - timedelta(weeks=config.calibration_weeks)
        )
        if past["happened"].sum() >= MIN_CALIBRATION_POSITIVES:
            chance = apply_platt(chance, *fit_platt(past["chance"].to_numpy(),
                                                    past["happened"].to_numpy()))
    why = reasons(fitted.contributions(live))
    state = labels.filter(pl.col("week_start") == origin).select(
        "port_id",
        pl.col("y").alias("last_calls"),
        pl.col("base").alias("normal_calls"),
        pl.col("drop_pct").alias("last_drop_pct"),
    )
    warn = (
        live.select("origin_week", "target_week", "horizon", "port_id", "size_band", "base_rate")
        .with_columns(pl.Series("chance", chance), pl.Series("reasons", why))
        .join(state, on="port_id", how="left")
        .join(
            profiles.select("port_id", "port_name", "country", "continent", "latitude",
                            "longitude", "industry_top1"),
            on="port_id", how="left",
        )
        .with_columns((pl.col("chance") / pl.col("base_rate")).alias("lift"))
    )
    warn = warn.with_columns(flag_top(warn).alias("flagged"))
    warn = warn.join(alternatives(warn), on=["horizon", "port_id"], how="left")
    return Forecast(
        origin=origin,
        release=origin + timedelta(days=RELEASE_LAG_DAYS),
        warnings=warn.sort("horizon", "chance", descending=[False, True]),
        rounds=fitted.rounds,
        trained_rows=known.height,
    )


# --- history -----------------------------------------------------------------

HISTORY_COLUMNS = ["origin_week", "target_week", "horizon", "port_id", "chance", "flagged",
                   "source", "generated_at"]


def to_history(rows: pl.DataFrame, source: str) -> pl.DataFrame:
    """Rows in the history's shape; ``rows`` needs keys and ``chance``."""
    if "flagged" not in rows.columns:
        rows = rows.with_columns(flag_top(rows).alias("flagged"))
    return rows.select(
        "origin_week", "target_week", pl.col("horizon").cast(pl.Int8), "port_id",
        pl.col("chance").cast(pl.Float64), "flagged",
        pl.lit(source).alias("source"),
        pl.lit(datetime.now(UTC).replace(tzinfo=None)).alias("generated_at"),
    )


def merge_history(old: pl.DataFrame | None, new: pl.DataFrame) -> pl.DataFrame:
    """New rows replace old ones for the same origin (a rerun of a week)."""
    if old is None or old.is_empty():
        return new.sort("origin_week", "horizon", "port_id")
    kept = old.filter(~pl.col("origin_week").is_in(new["origin_week"].unique().implode()))
    return pl.concat([kept.select(HISTORY_COLUMNS), new]).sort("origin_week", "horizon", "port_id")


def read_history() -> pl.DataFrame | None:
    from huggingface_hub import hf_hub_download
    from huggingface_hub.errors import EntryNotFoundError, RepositoryNotFoundError

    try:
        path = hf_hub_download(HISTORY_REPO, HISTORY_FILE, repo_type="dataset")
    except (EntryNotFoundError, RepositoryNotFoundError):
        return None
    return pl.read_parquet(path)


def write_history(history: pl.DataFrame, message: str) -> None:
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(HISTORY_REPO, repo_type="dataset", private=False, exist_ok=True)
    buf = io.BytesIO()
    history.write_parquet(buf, compression="zstd")
    api.upload_file(path_or_fileobj=buf.getvalue(), path_in_repo=HISTORY_FILE,
                    repo_id=HISTORY_REPO, repo_type="dataset", commit_message=message)


def publish_space(site: Path, message: str) -> None:
    """Upload the built page to the static HF Space."""
    from huggingface_hub import HfApi

    api = HfApi()
    api.create_repo(SPACE_REPO, repo_type="space", space_sdk="static", private=False,
                    exist_ok=True)
    (site / "README.md").write_text(SPACE_README, encoding="utf-8")
    api.upload_folder(folder_path=str(site), repo_id=SPACE_REPO, repo_type="space",
                      commit_message=message)
    (site / "README.md").unlink()


def track_record(history: pl.DataFrame, labels: pl.DataFrame) -> pl.DataFrame:
    """Past warnings joined to what happened (target weeks with a label)."""
    outcome = labels.filter(pl.col("is_disruption").is_not_null()).select(
        pl.col("week_start").alias("target_week"), "port_id",
        pl.col("is_disruption").alias("happened"), pl.col("drop_pct"),
    )
    return history.join(outcome, on=["target_week", "port_id"], how="inner")


def _json_safe(v: Any) -> Any:
    if isinstance(v, float) and not math.isfinite(v):
        return None
    if isinstance(v, date):
        return v.isoformat()
    return v


def rows_json(frame: pl.DataFrame) -> list[dict[str, Any]]:
    return [{k: _json_safe(v) for k, v in r.items()} for r in frame.to_dicts()]


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Live port disruption warning")
    parser.add_argument("--hf", action="store_true", help="read data from HF")
    parser.add_argument("--site", help="write the dashboard into this folder")
    parser.add_argument("--publish", action="store_true",
                        help="append this week's warnings to the HF history")
    parser.add_argument("--space", action="store_true",
                        help="also upload the page to the HF Space (needs --site)")
    parser.add_argument("--seed-history",
                        help="start the HF history from this backtest parquet, then exit")
    args = parser.parse_args(argv)

    if args.seed_history:
        rows = pl.read_parquet(args.seed_history).rename({MODEL: "chance"})
        history = to_history(rows, "backtest")
        write_history(merge_history(read_history(), history),
                      f"Seed from the backtest: {history.height:,} rows")
        print(f"seeded {history.height:,} rows")
        return

    from src.ml.disruption_warning import dashboard

    weekly, events, profiles, chokepoints = load_inputs(args.hf)
    old = read_history()
    fc = forecast(weekly, events, profiles, chokepoints, history=old)
    print(f"origin {fc.origin} (release {fc.release}); {fc.rounds} rounds on "
          f"{fc.trained_rows:,} rows; {int(fc.warnings['flagged'].sum())} ports flagged")
    history = merge_history(old, to_history(fc.warnings, "live"))
    if args.publish:
        write_history(history, f"Warnings for {fc.origin}")
    if args.site:
        labels = label_drops(weekly_grid(weekly))
        out = Path(args.site)
        out.mkdir(parents=True, exist_ok=True)
        page = dashboard.render(fc, track_record(history, labels), history)
        (out / "index.html").write_text(page, encoding="utf-8")
        print(f"wrote {out / 'index.html'} ({len(page) / 1024:.0f} KB)")
        if args.space:
            publish_space(out, f"Warnings for {fc.origin}")


if __name__ == "__main__":
    main()
