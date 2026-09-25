"""Train and backtest a LightGBM model of weekly port calls.

    python -m src.ml.port_forecast.train --hf
    python -m http.server 8765 -d storage/ml_runs   # then open http://localhost:8765

One model covers every port and horizon. It is retrained on an expanding window
every ``retrain_weeks`` and scored on the same origins, ports and horizons as the
baselines, so the comparison is like for like.

No look-ahead: a model trained at cutoff ``C`` sees only rows whose target week
is on or before ``C`` (their actual was known by then), and forecasts origins
from ``C`` until the next retrain. The last ``valid_weeks`` before ``C`` are held
out for early stopping.

Progress (curves, fold scores, feature importance) is written to
``progress.json`` in the run folder as training goes; ``live.html`` in the same
folder charts it.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import duckdb
import polars as pl

from src.config import settings
from src.ml.port_forecast.backtest import HF_REPO, load_weekly_hf, load_weekly_local
from src.ml.port_forecast.baselines import (
    BASELINES,
    backtest_origins,
    baseline_forecasts,
    weekly_grid,
)
from src.ml.port_forecast.evaluate import REFERENCE, score
from src.ml.port_forecast.features import CATEGORICAL, FEATURES, build_features
from src.storage.writer import get_db_path

MODEL = "lightgbm"
LIVE_PAGE = Path(__file__).with_name("live.html")


@dataclass
class TrainConfig:
    eval_start: date = date(2023, 1, 2)
    eval_every_weeks: int = 4
    #: Training origins: every N weeks from train_start. Must divide
    #: eval_every_weeks and line up with eval_start so eval rows are included.
    train_start: date = date(2020, 1, 6)
    train_every_weeks: int = 2
    retrain_weeks: int = 12
    valid_weeks: int = 8
    num_boost_round: int = 3000
    early_stopping_rounds: int = 150
    params: dict[str, Any] = field(
        default_factory=lambda: {
            # L1 on the log change, weighted by the port's recent level, is
            # close to minimising total absolute error in calls (WAPE).
            "objective": "l1",
            "metric": "None",
            "learning_rate": 0.05,
            "num_leaves": 63,
            "min_data_in_leaf": 200,
            "feature_fraction": 0.8,
            "bagging_fraction": 0.8,
            "bagging_freq": 1,
            "lambda_l2": 1.0,
            "verbosity": -1,
            "seed": 7,
        }
    )


class Progress:
    """The run's state as JSON, rewritten atomically so the page never reads
    half a file."""

    def __init__(self, run_dir: Path, config: TrainConfig) -> None:
        self.path = run_dir / "progress.json"
        self.state: dict[str, Any] = {
            "status": "loading data",
            "started": datetime.now(UTC).isoformat(timespec="seconds"),
            "updated": None,
            "config": json.loads(json.dumps(asdict(config), default=str)),
            "data": {},
            "folds": [],
            "current": None,
            "scores": {},
            "importance": [],
            "reference": REFERENCE,
            "log": [],
        }
        self.save()

    def log(self, msg: str) -> None:
        stamp = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{stamp}] {msg}", flush=True)
        self.state["log"] = [*self.state["log"], f"{stamp} {msg}"][-200:]
        self.save()

    def save(self) -> None:
        self.state["updated"] = datetime.now(UTC).isoformat(timespec="seconds")
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, default=str), encoding="utf-8")
        os.replace(tmp, self.path)


def _to_numpy(frame: pl.DataFrame) -> Any:
    # Categories go in as their integer codes; LightGBM is told which they are.
    cols = [pl.col(c).to_physical() if c in CATEGORICAL else pl.col(c) for c in FEATURES]
    return frame.select([c.cast(pl.Float32) for c in cols]).to_numpy()


def _wape_eval(level: dict[int, Any]) -> Any:
    """LightGBM eval: WAPE in calls. Labels and predictions are log changes
    from the 4-week average, so both are turned back into calls first."""
    import numpy as np

    def feval(preds: Any, data: Any) -> tuple[str, float, bool]:
        base = level[id(data)]
        actual = np.expm1(data.get_label() + base)
        pred = np.clip(np.expm1(preds + base), 0, None)
        return "wape", float(np.abs(pred - actual).sum() / max(actual.sum(), 1.0)), False

    return feval


def _live_callback(progress: Progress, every: int = 10) -> Any:
    def callback(env: Any) -> None:
        cur = progress.state["current"]
        for name, _metric, value, _ in env.evaluation_result_list:
            cur["curve"].setdefault(name, []).append(round(value, 5))
        cur["iteration"] = env.iteration + 1
        if (env.iteration + 1) % every == 0:
            progress.save()

    return callback


def train_and_backtest(
    weekly: pl.DataFrame, profiles: pl.DataFrame, config: TrainConfig, progress: Progress
) -> pl.DataFrame:
    import lightgbm as lgb
    import numpy as np

    grid = weekly_grid(weekly)
    eval_origins = backtest_origins(grid, config.eval_start, config.eval_every_weeks)
    last_week = eval_origins[-1] + timedelta(weeks=4)
    train_origins = [
        config.train_start + timedelta(weeks=k)
        for k in range(0, (last_week - config.train_start).days // 7 + 1, config.train_every_weeks)
    ]
    missing = set(eval_origins) - set(train_origins)
    if missing:
        raise ValueError(f"eval origins not on the training grid: {sorted(missing)[:3]}")

    progress.log("building features")
    fx = build_features(grid, profiles, train_origins)
    baselines = baseline_forecasts(grid, eval_origins)
    progress.state["data"] = {
        "rows": fx.height,
        "ports": fx["port_id"].n_unique(),
        "features": FEATURES,
        "train_origins": len(train_origins),
        "eval_origins": len(eval_origins),
        "first_eval": eval_origins[0],
        "last_eval": eval_origins[-1],
    }
    progress.log(f"{fx.height:,} feature rows, {len(eval_origins)} eval origins")

    cutoffs = eval_origins[:: config.retrain_weeks // config.eval_every_weeks]
    progress.state["n_folds"] = len(cutoffs)
    predictions = []
    importance = np.zeros(len(FEATURES))
    for i, cutoff in enumerate(cutoffs):
        t0 = time.perf_counter()
        next_cutoff = cutoffs[i + 1] if i + 1 < len(cutoffs) else date.max
        valid_from = cutoff - timedelta(weeks=config.valid_weeks)
        known = fx.filter(pl.col("target_week") <= cutoff)
        tr = known.filter(pl.col("target_week") <= valid_from)
        va = known.filter(pl.col("target_week") > valid_from)
        # Train curve on a fixed sample: scoring 1M+ rows every round is slow.
        tr_sample = tr.sample(n=min(50_000, tr.height), seed=i)
        test = fx.filter(
            pl.col("origin_week").is_in([o for o in eval_origins if cutoff <= o < next_cutoff])
        )

        level: dict[int, Any] = {}

        def dataset(frame: pl.DataFrame, ref: Any = None) -> Any:
            ds = lgb.Dataset(
                _to_numpy(frame),
                label=frame["target"].to_numpy(),
                weight=(frame["mean_4"] + 1).to_numpy(),
                feature_name=FEATURES,
                categorical_feature=CATEGORICAL,
                reference=ref,
                free_raw_data=False,
            )
            level[id(ds)] = np.log1p(frame["mean_4"].to_numpy())
            return ds

        d_tr = dataset(tr)
        d_va, d_sample = dataset(va, d_tr), dataset(tr_sample, d_tr)
        progress.state["current"] = {
            "fold": i + 1,
            "cutoff": cutoff,
            "n_train": tr.height,
            "n_valid": va.height,
            "n_test": test.height,
            "iteration": 0,
            "curve": {},
        }
        progress.state["status"] = f"training fold {i + 1} of {len(cutoffs)}"
        progress.log(
            f"fold {i + 1}/{len(cutoffs)}: cutoff {cutoff}, train {tr.height:,}, "
            f"valid {va.height:,}, test {test.height:,}"
        )
        booster = lgb.train(
            config.params,
            d_tr,
            num_boost_round=config.num_boost_round,
            valid_sets=[d_sample, d_va],
            valid_names=["train", "valid"],
            feval=_wape_eval(level),
            callbacks=[
                lgb.early_stopping(config.early_stopping_rounds, verbose=False),
                _live_callback(progress),
            ],
        )
        raw = booster.predict(_to_numpy(test), num_iteration=booster.best_iteration)
        pred = test.select("origin_week", "horizon", "port_id").with_columns(
            pl.Series(MODEL, np.clip(np.expm1(raw + np.log1p(test["mean_4"].to_numpy())), 0, None))
        )
        predictions.append(pred)
        gain = booster.feature_importance("gain", iteration=booster.best_iteration)
        importance += gain / max(gain.sum(), 1e-9)

        fold_rows = baselines.join(pred, on=["origin_week", "horizon", "port_id"])
        fold_score = score(fold_rows, [MODEL, *BASELINES], by=["horizon"]).to_dicts()
        curve = progress.state["current"]["curve"]
        progress.state["folds"].append(
            {
                **{k: v for k, v in progress.state["current"].items() if k != "curve"},
                "best_iteration": booster.best_iteration,
                "best_valid_wape": min(curve.get("valid", [float("nan")])),
                "seconds": round(time.perf_counter() - t0, 1),
                "test_origins": test["origin_week"].n_unique(),
                "by_horizon": fold_score,
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
        _update_scores(progress, baselines, pl.concat(predictions))
        progress.log(
            f"fold {i + 1} done: {booster.best_iteration} rounds, "
            f"{progress.state['folds'][-1]['seconds']}s"
        )

    progress.state["status"] = "done"
    progress.state["current"] = None
    progress.log("done")
    return baselines.join(pl.concat(predictions), on=["origin_week", "horizon", "port_id"])


def _update_scores(progress: Progress, baselines: pl.DataFrame, preds: pl.DataFrame) -> None:
    rows = baselines.join(preds, on=["origin_week", "horizon", "port_id"]).with_columns(
        pl.col("origin_week").dt.year().alias("year")
    )
    models = [MODEL, *BASELINES]
    progress.state["scores"] = {
        "n": rows.height,
        "by_horizon": score(rows, models, by=["horizon"]).to_dicts(),
        "by_size": score(rows, models, by=["size_band"]).to_dicts(),
        "by_year": score(rows, models, by=["year"]).to_dicts(),
        "by_origin": score(rows, models, by=["origin_week"]).to_dicts(),
    }
    progress.save()


def load_profiles(hf: bool) -> pl.DataFrame:
    if not hf:
        with duckdb.connect(str(get_db_path()), read_only=True) as conn:
            return conn.execute("SELECT * FROM port_profiles").pl()
    from huggingface_hub import get_token

    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    token = os.environ.get("HF_TOKEN") or get_token()
    if token:
        conn.execute(f"CREATE SECRET hf (TYPE huggingface, TOKEN '{token}')")
    return conn.execute(
        f"SELECT * FROM read_parquet('hf://datasets/{HF_REPO}/port_profiles/port_profiles.parquet')"
    ).pl()


def new_run_dir(root: Path | None = None) -> Path:
    root = root or settings.storage_dir / "ml_runs"
    run = root / datetime.now().strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True)
    # The page reads progress.json next to it, or, at the root, the run named
    # in latest.txt (a pointer file, since symlinks need admin on Windows).
    shutil.copy(LIVE_PAGE, run / "index.html")
    shutil.copy(LIVE_PAGE, root / "index.html")
    (root / "latest.txt").write_text(run.name, encoding="utf-8")
    return run


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Train and backtest the port-call model")
    parser.add_argument("--hf", action="store_true", help="read data from HF")
    parser.add_argument("--train-every-weeks", type=int, default=2)
    parser.add_argument("--out", help="also write the backtest forecasts to this parquet")
    args = parser.parse_args(argv)

    config = TrainConfig(train_every_weeks=args.train_every_weeks)
    run_dir = new_run_dir()
    progress = Progress(run_dir, config)
    progress.log(f"run folder {run_dir}")
    weekly = load_weekly_hf() if args.hf else load_weekly_local()
    profiles = load_profiles(args.hf)
    try:
        result = train_and_backtest(weekly, profiles, config, progress)
    except Exception as e:
        progress.state["status"] = f"failed: {e}"
        progress.log(f"failed: {e!r}")
        raise
    if args.out:
        result.write_parquet(args.out)


if __name__ == "__main__":
    main()
