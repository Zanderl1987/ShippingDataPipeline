"""Train and backtest a LightGBM disruption classifier.

    python -m src.ml.disruption_warning.train --hf
    python -m http.server 8765 -d storage/ml_runs   # then open http://localhost:8765/disruption/

One model covers every port and horizon (``horizon`` is an input). It is
retrained on an expanding window every ``retrain_weeks`` and scored on the
same rows as the rules in ``baselines``, so the comparison is like for like.

No look-ahead: at cutoff ``C`` (an origin week) the release that completes
week ``C`` is out, so every row whose target week is on or before ``C`` has a
known label; the model trains on those and warns for origins from ``C`` until
the next retrain. The number of boosting rounds is picked on the last
``valid_weeks`` of targets before ``C``, then the model is refit on all known
rows with that many rounds.

Progress is written to ``progress.json`` in the run folder as training goes;
``live.html`` next to this file charts it.
"""
from __future__ import annotations

import argparse
import math
import shutil
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from src.config import settings
from src.ml.disruption_warning.backtest import tune_radius
from src.ml.disruption_warning.baselines import RULES, rule_scores, weekly_origins
from src.ml.disruption_warning.evaluate import BUDGET, average_precision, score
from src.ml.disruption_warning.features import (
    CATEGORICAL,
    FEATURES,
    build_features,
    load_inputs,
)
from src.ml.disruption_warning.labels import label_drops
from src.ml.port_forecast.baselines import weekly_grid
from src.ml.port_forecast.train import Progress

MODEL = "lightgbm"
#: The rules the goal is set against: the better of the two, per group.
BEST_RULES = ["last_z", "persistence_or_gdacs"]
GOAL_AP = 0.045
GOAL_PRECISION = 0.15
LIVE_PAGE = Path(__file__).with_name("live.html")
MIN_CALIBRATION_POSITIVES = 100
CALIBRATION_BINS = [0.0, 0.005, 0.01, 0.02, 0.05, 0.1, 0.2, 0.4, 1.0]


@dataclass
class TrainConfig:
    train_start: date = date(2020, 1, 6)
    eval_start: date = date(2023, 1, 2)
    #: Last origin scored (exclusive); lets settings be tuned on early years only.
    eval_end: date = date.max
    retrain_weeks: int = 12
    #: Rare positives: ~26 weeks of targets hold ~500 disruptions, enough to
    #: pick the number of rounds without chasing noise.
    valid_weeks: int = 26
    num_boost_round: int = 2000
    early_stopping_rounds: int = 150
    #: Weight training rows by recency: a row this many weeks older than the
    #: cutoff counts half. None weighs all rows the same.
    half_life_weeks: float | None = None
    #: Rescale scores into chances with a logistic fit: "valid" on the
    #: validation weeks, "history" on the model's own earlier out-of-sample
    #: predictions whose outcomes are known (last ``calibration_weeks``; falls
    #: back to "valid" until they hold ``MIN_CALIBRATION_POSITIVES``), None off.
    calibrate: str | None = "history"
    calibration_weeks: int = 52
    params: dict[str, Any] = field(
        default_factory=lambda: {
            "objective": "binary",
            "metric": "average_precision",
            "learning_rate": 0.03,
            "num_leaves": 31,
            "min_data_in_leaf": 300,
            "feature_fraction": 0.7,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "lambda_l2": 5.0,
            "verbosity": -1,
            "seed": 7,
        }
    )


def _to_numpy(frame: pl.DataFrame) -> Any:
    # Categories go in as their integer codes; LightGBM is told which they are.
    cols = [pl.col(c).to_physical() if c in CATEGORICAL else pl.col(c) for c in FEATURES]
    return frame.select([c.cast(pl.Float32) for c in cols]).to_numpy()


def _fmt(x: float | None) -> str:
    return "-" if x is None else f"{x:.3f}"


def _live_callback(progress: Progress, every: int = 10) -> Any:
    def callback(env: Any) -> None:
        cur = progress.state["current"]
        for name, _metric, value, _ in env.evaluation_result_list:
            cur["curve"].setdefault(name, []).append(round(value, 5))
        cur["iteration"] = env.iteration + 1
        if (env.iteration + 1) % every == 0:
            progress.save()

    return callback


def _weights(frame: pl.DataFrame, cutoff: date, half_life: float | None) -> Any:
    if half_life is None:
        return None
    age = frame.select((pl.lit(cutoff) - pl.col("target_week")).dt.total_days())
    weeks = age.to_series().to_numpy() / 7
    return 0.5 ** (weeks / half_life)


def _logit(p: Any) -> Any:
    import numpy as np

    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p / (1 - p))


def fit_platt(pred: Any, y: Any, steps: int = 200) -> tuple[float, float]:
    """Fit ``chance = sigmoid(a * logit(pred) + b)`` by Newton's method.
    Monotone, so it changes the chances quoted but not the ranking."""
    import numpy as np

    x, y = _logit(pred), np.asarray(y, dtype=float)
    a, b = 1.0, 0.0
    for _ in range(steps):
        p = 1 / (1 + np.exp(-(a * x + b)))
        w = p * (1 - p) + 1e-9
        g = np.array([np.sum((p - y) * x), np.sum(p - y)])
        h = np.array([[np.sum(w * x * x), np.sum(w * x)], [np.sum(w * x), np.sum(w)]])
        step = np.linalg.solve(h + 1e-6 * np.eye(2), g)
        a, b = a - step[0], b - step[1]
        if np.abs(step).max() < 1e-8:
            break
    return float(a), float(b)


def apply_platt(pred: Any, a: float, b: float) -> Any:
    import numpy as np

    return 1 / (1 + np.exp(-(a * _logit(pred) + b)))


@dataclass
class Fitted:
    booster: Any
    rounds: int
    #: Platt scaling (``apply_platt``); (1, 0) leaves scores as they are.
    platt: tuple[float, float]
    n_valid: int

    def raw(self, frame: pl.DataFrame) -> Any:
        return self.booster.predict(_to_numpy(frame))

    def predict(self, frame: pl.DataFrame) -> Any:
        return apply_platt(self.raw(frame), *self.platt)

    def contributions(self, frame: pl.DataFrame) -> Any:
        """Each feature's push on the log-odds, per row (last column: bias)."""
        return self.booster.predict(_to_numpy(frame), pred_contrib=True)


def fit_model(
    known: pl.DataFrame,
    cutoff: date,
    config: TrainConfig,
    seed: int = 0,
    callbacks: list[Any] | None = None,
    on_split: Any = None,
) -> Fitted:
    """Pick the number of rounds on the last ``valid_weeks`` of targets before
    ``cutoff``, fit the calibration there, then refit on every known row."""
    import lightgbm as lgb

    valid_from = cutoff - timedelta(weeks=config.valid_weeks)
    tr = known.filter(pl.col("target_week") <= valid_from)
    va = known.filter(pl.col("target_week") > valid_from)
    if on_split is not None:
        on_split(tr, va)

    def dataset(frame: pl.DataFrame, ref: Any = None) -> Any:
        return lgb.Dataset(
            _to_numpy(frame),
            label=frame["y"].cast(pl.Int8).to_numpy(),
            weight=_weights(frame, cutoff, config.half_life_weeks),
            feature_name=FEATURES,
            categorical_feature=CATEGORICAL,
            reference=ref,
            free_raw_data=False,
        )

    d_tr = dataset(tr)
    sample = tr.sample(n=min(60_000, tr.height), seed=seed)
    picked = lgb.train(
        config.params,
        d_tr,
        num_boost_round=config.num_boost_round,
        valid_sets=[dataset(sample, d_tr), dataset(va, d_tr)],
        valid_names=["train", "valid"],
        callbacks=[
            lgb.early_stopping(config.early_stopping_rounds, verbose=False),
            *(callbacks or []),
        ],
    )
    rounds = max(1, picked.best_iteration)
    platt = (1.0, 0.0)
    if config.calibrate is not None:
        platt = fit_platt(picked.predict(_to_numpy(va), num_iteration=rounds), va["y"].to_numpy())
    booster = lgb.train(config.params, dataset(known), num_boost_round=rounds)
    return Fitted(booster, rounds, platt, va.height)


def goal_check(rows: pl.DataFrame, by: list[str] | None = None) -> pl.DataFrame:
    """Per group: the model's AP, the best rule's AP, their ratio, and the
    model's precision and recall at the alert budget."""
    keys = by or ["_all"]
    table = score(rows.with_columns(pl.lit(0).alias("_all")), [MODEL, *RULES], by=keys)
    model = table.filter(pl.col("model") == MODEL).select(
        *keys, "n", "rate", pl.col("ap").alias("ap_model"), "precision", "recall", "big_recall"
    )
    best = (
        table.filter(pl.col("model").is_in(BEST_RULES))
        .sort("ap", descending=True, nulls_last=True)
        .group_by(keys, maintain_order=True)
        .agg(pl.col("model").first().alias("best_rule"), pl.col("ap").first().alias("ap_rule"))
    )
    out = model.join(best, on=keys, how="left").sort(keys)
    out = out.with_columns((pl.col("ap_model") / pl.col("ap_rule")).alias("ratio"))
    return out.drop("_all") if by is None else out


def pr_curve(rows: pl.DataFrame, models: list[str], points: int = 60) -> list[dict[str, Any]]:
    """Precision and recall when flagging the top k rows, k on a log scale
    from ~0.05% to 20% of rows (ties broken by row order)."""
    positives = int(rows["y"].sum())
    if not positives:
        return []
    ks = sorted({max(1, int(rows.height * 10 ** (-3.3 + 2.6 * i / (points - 1))))
                 for i in range(points)})
    out = []
    for m in models:
        hits = rows.sort(m, descending=True)["y"].cast(pl.Int64).cum_sum()
        for k in ks:
            tp = int(hits[k - 1])
            out.append({"model": m, "share": k / rows.height, "precision": tp / k,
                        "recall": tp / positives})
    return out


def calibration(rows: pl.DataFrame) -> pl.DataFrame:
    """Predicted chance vs how often a disruption followed, in bins."""
    labels = [f"{a:g}-{b:g}" for a, b in zip(CALIBRATION_BINS, CALIBRATION_BINS[1:], strict=False)]
    return (
        rows.with_columns(
            pl.col(MODEL).cut(CALIBRATION_BINS[1:-1], labels=labels, left_closed=True).alias("bin")
        )
        .group_by("bin")
        .agg(
            pl.len().alias("n"),
            pl.col(MODEL).mean().alias("predicted"),
            pl.col("y").cast(pl.Float64).mean().alias("actual"),
        )
        .sort("bin")
    )


def _update_scores(progress: Progress, rows: pl.DataFrame) -> None:
    rows = rows.with_columns(pl.col("origin_week").dt.year().alias("year"))
    models = [MODEL, *RULES]
    near = rows.filter(pl.col("horizon") <= 2)
    progress.state["scores"] = {
        "n": rows.height,
        "positives": int(rows["y"].sum()),
        "all": goal_check(rows).to_dicts(),
        "near": goal_check(near).to_dicts(),
        "by_horizon": goal_check(rows, ["horizon"]).to_dicts(),
        "by_year": goal_check(rows, ["year"]).to_dicts(),
        "by_size": goal_check(rows, ["size_band"]).to_dicts(),
        "models": score(rows, models).to_dicts(),
        "pr_curve": pr_curve(rows, [MODEL, "last_z", "persistence_or_gdacs"]),
        "calibration": calibration(rows).with_columns(pl.col("bin").cast(pl.Utf8)).to_dicts(),
    }
    progress.save()


def train_and_backtest(
    fx: pl.DataFrame, radius_km: float, config: TrainConfig, progress: Progress
) -> pl.DataFrame:
    import numpy as np

    eval_origins = sorted(
        o for o in fx["origin_week"].unique().to_list()
        if config.eval_start <= o < config.eval_end
    )
    cutoffs = eval_origins[:: config.retrain_weeks]
    progress.state["n_folds"] = len(cutoffs)
    progress.state["data"] = {
        "rows": fx.height,
        "ports": fx["port_id"].n_unique(),
        "features": FEATURES,
        "eval_origins": len(eval_origins),
        "first_eval": eval_origins[0],
        "last_eval": eval_origins[-1],
        "radius_km": radius_km,
        "budget": BUDGET,
        "goal_ap": GOAL_AP,
        "goal_precision": GOAL_PRECISION,
    }
    predictions: list[pl.DataFrame] = []
    importance = np.zeros(len(FEATURES))

    for i, cutoff in enumerate(cutoffs):
        t0 = time.perf_counter()
        next_cutoff = cutoffs[i + 1] if i + 1 < len(cutoffs) else config.eval_end
        known = fx.filter(pl.col("target_week") <= cutoff)
        test = fx.filter((pl.col("origin_week") >= cutoff) & (pl.col("origin_week") < next_cutoff))
        progress.state["current"] = {
            "fold": i + 1,
            "cutoff": cutoff,
            "n_train": known.height,
            "n_valid": 0,
            "n_test": test.height,
            "positives_train": int(known["y"].sum()),
            "iteration": 0,
            "curve": {},
        }
        progress.state["status"] = f"training fold {i + 1} of {len(cutoffs)}"
        fitted = fit_model(
            known, cutoff, config, seed=i, callbacks=[_live_callback(progress)],
            on_split=lambda tr, va, i=i: progress.log(
                f"fold {i + 1}/{len(cutoffs)}: cutoff {cutoff}, train {tr.height:,}, "
                f"valid {va.height:,} ({int(va['y'].sum())} disruptions), test {test.height:,}"
            ),
        )
        rounds, booster = fitted.rounds, fitted.booster
        progress.state["current"]["n_valid"] = fitted.n_valid
        raw = fitted.raw(test)
        platt = fitted.platt
        if config.calibrate == "history" and predictions:
            past = pl.concat(predictions).filter(
                (pl.col("target_week") <= cutoff)
                & (pl.col("target_week") > cutoff - timedelta(weeks=config.calibration_weeks))
            )
            if past["y"].sum() >= MIN_CALIBRATION_POSITIVES:
                platt = fit_platt(past["raw"].to_numpy(), past["y"].to_numpy())
        pred = test.with_columns(pl.Series("raw", raw), pl.Series(MODEL, apply_platt(raw, *platt)))
        predictions.append(pred)
        gain = booster.feature_importance("gain")
        importance += gain / max(gain.sum(), 1e-9)

        scored = rule_scores(pred, radius_km)
        fold_all = goal_check(scored).row(0, named=True)
        curve = progress.state["current"]["curve"]
        progress.state["folds"].append(
            {
                **{k: v for k, v in progress.state["current"].items() if k != "curve"},
                "best_iteration": rounds,
                "best_valid_ap": max(curve.get("valid", [math.nan])),
                "seconds": round(time.perf_counter() - t0, 1),
                "test_origins": test["origin_week"].n_unique(),
                "test_positives": int(test["y"].sum()),
                "ap_model": fold_all["ap_model"],
                "ap_rule": fold_all["ap_rule"],
                "ap_last_z": average_precision(scored, "last_z"),
                "precision": fold_all["precision"],
                "curve": {k: v[:: max(1, len(v) // 150)] for k, v in curve.items()},
            }
        )
        progress.state["importance"] = sorted(
            (
                {"feature": f, "share": round(float(s) / (i + 1), 4)}
                for f, s in zip(FEATURES, importance, strict=True)
            ),
            key=lambda r: -r["share"],
        )
        _update_scores(progress, rule_scores(pl.concat(predictions).drop("raw"), radius_km))
        progress.log(
            f"fold {i + 1} done: {rounds} rounds, AP {_fmt(fold_all['ap_model'])} vs "
            f"{fold_all['best_rule']} {_fmt(fold_all['ap_rule'])}, "
            f"{progress.state['folds'][-1]['seconds']}s"
        )

    progress.state["status"] = "done"
    progress.state["current"] = None
    progress.log("done")
    return rule_scores(pl.concat(predictions).drop("raw"), radius_km)


def new_run_dir(root: Path | None = None) -> Path:
    root = root or settings.storage_dir / "ml_runs" / "disruption"
    run = root / datetime.now().strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True)
    # The page reads progress.json next to it, or, at the root, the run named
    # in latest.txt.
    shutil.copy(LIVE_PAGE, run / "index.html")
    shutil.copy(LIVE_PAGE, root / "index.html")
    (root / "latest.txt").write_text(run.name, encoding="utf-8")
    return run


def summary(rows: pl.DataFrame) -> dict[str, pl.DataFrame]:
    rows = rows.with_columns(pl.col("origin_week").dt.year().alias("year"))
    return {
        "all": goal_check(rows),
        "horizons 1-2 (precision goal)": goal_check(rows.filter(pl.col("horizon") <= 2)),
        "by horizon": goal_check(rows, ["horizon"]),
        "by year": goal_check(rows, ["year"]),
        "by port size": goal_check(rows, ["size_band"]),
        "every model": score(rows, [MODEL, *RULES]),
        "calibration": calibration(rows),
    }


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train and backtest the disruption classifier")
    parser.add_argument("--hf", action="store_true", help="read data from HF")
    parser.add_argument("--out", help="also write the scored backtest rows to this parquet")
    parser.add_argument("--calibrate", choices=["valid", "history", "none"],
                        default=TrainConfig.calibrate)
    args = parser.parse_args(argv)

    config = TrainConfig(calibrate=None if args.calibrate == "none" else args.calibrate)
    run_dir = new_run_dir()
    progress = Progress(run_dir, config)
    progress.state["reference"] = None
    progress.log(f"run folder {run_dir}")
    try:
        weekly, events, profiles, chokepoints = load_inputs(args.hf)
        progress.log("building features")
        origins = weekly_origins(label_drops(weekly_grid(weekly)), config.train_start)
        fx = build_features(weekly, events, profiles, chokepoints, origins)
        radius, _ = tune_radius(fx.filter(pl.col("origin_week") < config.eval_start))
        progress.log(f"{fx.height:,} rows, {len(origins)} origins; event radius {radius:.0f} km")
        result = train_and_backtest(fx, radius, config, progress)
    except Exception as e:
        progress.state["status"] = f"failed: {e}"
        progress.log(f"failed: {e!r}")
        raise
    with pl.Config(
        tbl_rows=100, tbl_cols=20, float_precision=3, tbl_hide_dataframe_shape=True,
        tbl_formatting="ASCII_MARKDOWN", tbl_hide_column_data_types=True,
    ):
        for name, table in summary(result).items():
            print(f"\n{name}\n{table}")
    if args.out:
        result.write_parquet(args.out)


if __name__ == "__main__":
    main()
