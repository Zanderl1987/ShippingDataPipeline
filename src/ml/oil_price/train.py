"""Walk-forward backtest: do tanker flows help predict Brent's next 1-4 weeks?

    python -m src.ml.oil_price.train          # reads HF, saves only the run folder
    # live page: http://localhost:8765/oil/  (python -m http.server 8765 -d storage/ml_runs)

Every ``retrain_weeks`` the models are refit on origins whose outcome was known
by the cutoff, then predict the following origins. Nested ridge models answer
the question in steps: price alone, + US stocks, + tanker flows and chokepoints
(``full``). A small LightGBM gets the full set too. Everything is scored
against "no change", the bar short-term oil forecasts usually fail to clear.

Scores:
- R² vs no change: 1 - SSE(model) / SSE(no change). Above 0 = better.
- Direction right: share of weeks where the predicted sign was right.
- Clark-West p: is the gain over no change bigger than luck? (one-sided, with
  Newey-West errors for overlapping 2- and 4-week targets).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import shutil
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from src.config import settings
from src.ml.oil_price.features import HORIZONS, build_features, feature_sets, fridays

LIVE_PAGE = Path(__file__).with_name("live.html")
RIDGES = {"ridge_price": "price", "ridge_price_stocks": "price_stocks", "ridge_full": "full"}
MODELS = ["no_change", "drift", *RIDGES, "lgb_full"]


@dataclass
class TrainConfig:
    first_origin: date = date(2020, 1, 3)
    eval_start: date = date(2022, 1, 7)
    retrain_weeks: int = 4
    valid_weeks: int = 52
    alphas: tuple[float, ...] = (1.0, 3.0, 10.0, 30.0, 100.0, 300.0, 1000.0, 3000.0)
    #: Training targets are clipped to these quantiles (COVID 2020, Hormuz 2026).
    clip_quantile: float = 0.02
    num_boost_round: int = 800
    early_stopping_rounds: int = 100
    lgb_params: dict[str, Any] = field(default_factory=lambda: {
        "objective": "huber", "alpha": 0.05, "metric": "l2", "learning_rate": 0.02,
        "num_leaves": 4, "min_data_in_leaf": 15, "feature_fraction": 0.6,
        "bagging_fraction": 0.8, "bagging_freq": 1, "lambda_l2": 5.0,
        "verbosity": -1, "seed": 7,
    })


class Progress:
    """The run's state as JSON, replaced atomically so the page never reads
    half a file. ``save(throttle=True)`` writes at most every 0.5 s."""

    def __init__(self, run_dir: Path, config: TrainConfig) -> None:
        self.path = run_dir / "progress.json"
        self._last = 0.0
        self.state: dict[str, Any] = {
            "status": "loading data", "started": _now(), "updated": None,
            "config": json.loads(json.dumps(asdict(config), default=str)),
            "models": MODELS, "horizons": list(HORIZONS),
            "data": {}, "folds": [], "current": None, "predictions": [],
            "prices": [], "importance": {}, "scores": {}, "latest": None,
            "last_curve": None, "log": [],
        }
        self.save()

    def log(self, msg: str) -> None:
        stamp = datetime.now(UTC).strftime("%H:%M:%S")
        print(f"[{stamp}] {msg}", flush=True)
        self.state["log"] = [*self.state["log"], f"{stamp} {msg}"][-300:]
        self.save()

    def save(self, throttle: bool = False) -> None:
        if throttle and time.monotonic() - self._last < 0.5:
            return
        self._last = time.monotonic()
        self.state["updated"] = _now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.state, default=str), encoding="utf-8")
        os.replace(tmp, self.path)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


# ── Models ────────────────────────────────────────────────────────────────


@dataclass
class Ridge:
    """Ridge regression on standardised inputs; missing inputs count as average.

    Inputs are capped at ``CAP`` standard deviations: in 2026 Hormuz tanker
    transits fell ~97% on the year, far outside anything in training, and a
    linear model would extrapolate that straight into the forecast.
    """

    CAP = 5.0

    alpha: float
    mean: Any = None
    scale: Any = None
    coef: Any = None
    intercept: float = 0.0

    def fit(self, x: Any, y: Any) -> Ridge:
        import numpy as np

        self.mean = np.nanmean(x, axis=0)
        self.mean = np.where(np.isnan(self.mean), 0.0, self.mean)
        sd = np.nanstd(x, axis=0)
        self.scale = np.where(np.isnan(sd) | (sd < 1e-12), 1.0, sd)
        z = self._z(x)
        self.intercept = float(y.mean())
        a = z.T @ z + self.alpha * np.eye(z.shape[1])
        self.coef = np.linalg.solve(a, z.T @ (y - self.intercept))
        return self

    def _z(self, x: Any) -> Any:
        import numpy as np

        z = np.clip((x - self.mean) / self.scale, -self.CAP, self.CAP)
        return np.where(np.isnan(z), 0.0, z)

    def predict(self, x: Any) -> Any:
        return self.intercept + self._z(x) @ self.coef


def _xy(rows: pl.DataFrame, cols: list[str], h: int) -> tuple[Any, Any]:
    x = rows.select(pl.col(cols).cast(pl.Float64)).to_numpy()
    return x, rows[f"y{h}"].to_numpy()


def _clip(y: Any, q: float) -> Any:
    import numpy as np

    lo, hi = np.quantile(y, [q, 1 - q])
    return np.clip(y, lo, hi)


def fit_ridge(
    inner: pl.DataFrame, valid: pl.DataFrame, known: pl.DataFrame,
    cols: list[str], h: int, config: TrainConfig,
) -> tuple[Ridge, float, float]:
    """Pick alpha on the validation year, then refit on everything known.
    Returns the model, the alpha and the validation R² vs no change."""

    xi, yi = _xy(inner, cols, h)
    xv, yv = _xy(valid, cols, h)
    best = (math.inf, config.alphas[-1])
    for alpha in config.alphas:
        m = Ridge(alpha).fit(xi, _clip(yi, config.clip_quantile))
        sse = float(((yv - m.predict(xv)) ** 2).sum())
        best = min(best, (sse, alpha))
    xk, yk = _xy(known, cols, h)
    model = Ridge(best[1]).fit(xk, _clip(yk, config.clip_quantile))
    valid_r2 = 1 - best[0] / max(float((yv**2).sum()), 1e-12)
    return model, best[1], valid_r2


def fit_lgb(
    inner: pl.DataFrame, valid: pl.DataFrame, known: pl.DataFrame,
    cols: list[str], h: int, config: TrainConfig, callback: Any = None,
) -> tuple[Any, int, Any]:
    """Pick the round count on the validation year, refit on everything known."""
    import lightgbm as lgb

    xi, yi = _xy(inner, cols, h)
    xv, yv = _xy(valid, cols, h)
    d_in = lgb.Dataset(xi, _clip(yi, config.clip_quantile), feature_name=cols,
                       free_raw_data=False)
    d_va = lgb.Dataset(xv, yv, reference=d_in, free_raw_data=False)
    callbacks: list[Any] = [lgb.early_stopping(config.early_stopping_rounds, verbose=False)]
    if callback is not None:
        callbacks.append(callback)
    probe = lgb.train(config.lgb_params, d_in, num_boost_round=config.num_boost_round,
                      valid_sets=[d_in, d_va], valid_names=["train", "valid"],
                      callbacks=callbacks)
    rounds = max(1, probe.best_iteration)
    xk, yk = _xy(known, cols, h)
    booster = lgb.train(config.lgb_params,
                        lgb.Dataset(xk, _clip(yk, config.clip_quantile), feature_name=cols),
                        num_boost_round=rounds)
    return booster, rounds, booster.feature_importance("gain")


# ── Scores ────────────────────────────────────────────────────────────────


def _phi(t: float) -> float:
    return 0.5 * (1 + math.erf(t / math.sqrt(2)))


def clark_west(y: Any, pred: Any, lag: int) -> tuple[float, float]:
    """Clark-West test of a model against "no change" (the nested zero
    forecast). Returns (t, one-sided p). Newey-West errors with ``lag``."""
    import numpy as np

    f = y**2 - ((y - pred) ** 2 - pred**2)
    n = f.size
    if n < 10:
        return math.nan, math.nan
    d = f - f.mean()
    var = float(d @ d) / n
    for k in range(1, lag + 1):
        var += 2 * (1 - k / (lag + 1)) * float(d[k:] @ d[:-k]) / n
    if var <= 0:
        return math.nan, math.nan
    t = float(f.mean() / np.sqrt(var / n))
    return t, 1 - _phi(t)


def score(preds: pl.DataFrame, by: list[str] | None = None) -> list[dict[str, Any]]:
    """One row per model x horizon (x ``by``)."""
    import numpy as np

    keys = ["h", *(by or [])]
    out = []
    for key, grp in preds.group_by(keys, maintain_order=True):
        y = grp["y"].to_numpy()
        sse0 = float((y**2).sum())
        h = int(grp["h"][0])
        for m in MODELS:
            p = grp[m].to_numpy()
            r2 = 1 - float(((y - p) ** 2).sum()) / sse0 if sse0 > 0 else math.nan
            nz = p != 0
            hit = float((np.sign(p[nz]) == np.sign(y[nz])).mean()) if nz.any() else None
            t, pv = clark_west(y, p, h - 1) if m != "no_change" else (math.nan, math.nan)
            out.append({
                **dict(zip(keys, key, strict=True)), "model": m, "n": int(y.size),
                "r2": round(r2, 4), "hit": None if hit is None else round(hit, 4),
                "cw_t": None if math.isnan(t) else round(t, 2),
                "cw_p": None if math.isnan(pv) else round(pv, 4),
            })
    return out


# ── Backtest ──────────────────────────────────────────────────────────────


def _live_callback(progress: Progress) -> Any:
    def callback(env: Any) -> None:
        cur = progress.state["current"]
        for name, _metric, value, _ in env.evaluation_result_list:
            cur["curve"].setdefault(name, []).append(round(value * 1e4, 4))  # in bp²
        cur["iteration"] = env.iteration + 1
        progress.save(throttle=True)

    return callback


def _known(fx: pl.DataFrame, h: int, by: date) -> pl.DataFrame:
    """Origins whose h-week outcome was public on ``by``."""
    return fx.filter(
        (pl.col("origin") + timedelta(weeks=h) <= by) & pl.col(f"y{h}").is_not_null()
    )


def _importance(acc: dict[int, Any], sets: dict[str, list[str]]) -> dict[str, Any]:
    """Average standardised ridge weight (full model) and LightGBM gain share."""
    out: dict[str, Any] = {}
    for h, parts in acc.items():
        if not parts["n"]:
            continue
        coef = parts["coef"] / parts["n"]
        gain = parts["gain"] / max(parts["gain"].sum(), 1e-12)
        rows = [
            {"feature": f, "coef": round(float(c), 5), "gain": round(float(g), 4)}
            for f, c, g in zip(sets["full"], coef, gain, strict=True)
        ]
        out[str(h)] = sorted(rows, key=lambda r: -abs(r["coef"]))
    return out


def train_and_backtest(
    fx: pl.DataFrame, config: TrainConfig, progress: Progress,
) -> pl.DataFrame:
    import numpy as np

    sets = feature_sets(fx)
    fx = fx.filter(pl.col("origin") >= config.first_origin)
    evals = fx.filter((pl.col("origin") >= config.eval_start) & pl.col("y1").is_not_null())
    eval_origins = evals["origin"].to_list()
    cutoffs = eval_origins[:: config.retrain_weeks]
    progress.state["n_folds"] = len(cutoffs) * len(HORIZONS)
    progress.state["data"].update({
        "origins": fx.height, "first_eval": eval_origins[0], "last_eval": eval_origins[-1],
        "features": {k: len(v) for k, v in sets.items()},
        "feature_names": sets["full"],
    })
    progress.log(
        f"{len(eval_origins)} test weeks {eval_origins[0]} → {eval_origins[-1]}, "
        f"{len(cutoffs)} retrains × {len(HORIZONS)} horizons; "
        f"features: price {len(sets['price'])}, +stocks {len(sets['price_stocks'])}, "
        f"full {len(sets['full'])}"
    )

    acc = {h: {"coef": np.zeros(len(sets["full"])), "gain": np.zeros(len(sets["full"])),
               "n": 0} for h in HORIZONS}
    predictions: list[pl.DataFrame] = []
    for i, cutoff in enumerate(cutoffs):
        nxt = cutoffs[i + 1] if i + 1 < len(cutoffs) else date.max
        for h in HORIZONS:
            t0 = time.perf_counter()
            known = _known(fx, h, cutoff)
            valid_from = cutoff - timedelta(weeks=config.valid_weeks)
            inner = _known(fx, h, valid_from)
            valid = known.filter(pl.col("origin") >= valid_from)
            test = evals.filter(
                (pl.col("origin") >= cutoff) & (pl.col("origin") < nxt)
                & pl.col(f"y{h}").is_not_null()
            )
            if test.is_empty():
                continue
            fold_no = len(progress.state["folds"]) + 1
            progress.state["current"] = {
                "fold": fold_no, "cutoff": cutoff, "h": h, "n_train": known.height,
                "iteration": 0, "curve": {},
            }
            progress.state["status"] = f"fold {fold_no} of {progress.state['n_folds']}"
            pred = {m: np.zeros(test.height) for m in MODELS}
            pred["drift"] += float(_clip(known[f"y{h}"].to_numpy(), config.clip_quantile).mean())
            alphas, valid_r2 = {}, {}
            for name, key in RIDGES.items():
                model, alphas[name], valid_r2[name] = fit_ridge(
                    inner, valid, known, sets[key], h, config)
                pred[name] = model.predict(_xy(test, sets[key], h)[0])
                if name == "ridge_full":
                    acc[h]["coef"] += model.coef
            booster, rounds, gain = fit_lgb(
                inner, valid, known, sets["full"], h, config, _live_callback(progress))
            acc[h]["gain"] += gain
            acc[h]["n"] += 1
            pred["lgb_full"] = booster.predict(_xy(test, sets["full"], h)[0])

            frame = test.select("origin", pl.lit(h).alias("h"), pl.col(f"y{h}").alias("y"),
                                pl.lit(cutoff).alias("cutoff")).with_columns(
                pl.Series(m, pred[m]) for m in MODELS)
            predictions.append(frame)
            progress.state["last_curve"] = {
                **{k: v for k, v in progress.state["current"].items() if k != "curve"},
                "curve": progress.state["current"]["curve"],
            }
            progress.state["predictions"].extend(
                {"origin": r["origin"].isoformat(), "h": h, "cutoff": cutoff.isoformat(),
                 "y": round(r["y"], 5), **{m: round(float(r[m]), 5) for m in MODELS}}
                for r in frame.iter_rows(named=True)
            )
            progress.state["folds"].append({
                "fold": fold_no, "cutoff": cutoff, "h": h, "n_train": known.height,
                "n_test": test.height, "alpha": alphas,
                "valid_r2": {k: round(v, 4) for k, v in valid_r2.items()},
                "rounds": rounds, "seconds": round(time.perf_counter() - t0, 2),
            })
            progress.state["importance"] = _importance(acc, sets)
        so_far = pl.concat(predictions)
        progress.state["scores"] = {"all": score(so_far)}
        progress.log(
            f"retrain {i + 1}/{len(cutoffs)} at {cutoff}: {known.height} weeks known; "
            + ", ".join(f"h{r['h']} full R² {r['r2']:+.3f}" for r in progress.state["scores"]["all"]
                        if r["model"] == "ridge_full")
        )

    result = pl.concat(predictions).with_columns(pl.col("origin").dt.year().alias("year"))
    progress.state["scores"] = {
        "all": score(result),
        "by_year": score(result, ["year"]),
        "no_2026": score(result.filter(pl.col("year") < 2026)),
    }
    progress.state["current"] = None
    # Late cutoffs have no finished 4-week (or 2-week) targets yet.
    progress.state["n_folds"] = len(progress.state["folds"])
    return result


def latest_forecast(fx: pl.DataFrame, config: TrainConfig) -> dict[str, Any]:
    """Refit on everything known and forecast from the newest origin."""
    sets = feature_sets(fx)
    fx = fx.filter(pl.col("origin") >= config.first_origin)
    row = fx.filter(pl.col("brent_usd").is_not_null()).tail(1)
    origin = row["origin"][0]
    out: dict[str, Any] = {"origin": origin, "brent": row["brent_usd"][0], "h": {}}
    for h in HORIZONS:
        known = _known(fx, h, origin)
        valid_from = origin - timedelta(weeks=config.valid_weeks)
        inner, valid = _known(fx, h, valid_from), known.filter(pl.col("origin") >= valid_from)
        preds = {"drift": float(_clip(known[f"y{h}"].to_numpy(), config.clip_quantile).mean())}
        for name, key in RIDGES.items():
            model, _, _ = fit_ridge(inner, valid, known, sets[key], h, config)
            preds[name] = float(model.predict(_xy(row, sets[key], h)[0])[0])
        booster, _, _ = fit_lgb(inner, valid, known, sets["full"], h, config)
        preds["lgb_full"] = float(booster.predict(_xy(row, sets["full"], h)[0])[0])
        out["h"][str(h)] = {k: round(v, 5) for k, v in preds.items()}
        out["h"][str(h)]["target"] = (origin + timedelta(weeks=h)).isoformat()
    return out


# ── CLI ───────────────────────────────────────────────────────────────────


def new_run_dir(root: Path | None = None) -> Path:
    root = root or settings.storage_dir / "ml_runs" / "oil"
    run = root / datetime.now().strftime("%Y%m%d-%H%M%S")
    run.mkdir(parents=True)
    # The page reads progress.json next to it, or, at the root, the run named
    # in latest.txt (a pointer file, since symlinks need admin on Windows).
    shutil.copy(LIVE_PAGE, run / "index.html")
    shutil.copy(LIVE_PAGE, root / "index.html")
    (root / "latest.txt").write_text(run.name, encoding="utf-8")
    return run


def _coverage(frames: dict[str, pl.DataFrame]) -> list[dict[str, Any]]:
    rows = []
    for name, df in frames.items():
        col = "price_date" if "price_date" in df.columns else "week_end"
        for series, grp in (df.group_by("series") if "series" in df.columns
                            else [((name,), df)]):
            rows.append({"source": name, "series": series[0], "first": grp[col].min(),
                         "last": grp[col].max(), "rows": grp.height})
    return sorted(rows, key=lambda r: (r["source"], r["series"]))


def main(argv: list[str] | None = None) -> None:
    from src.ml.oil_price import data

    parser = argparse.ArgumentParser(description="Backtest the Brent oil price models")
    parser.add_argument("--out", help="also write the backtest predictions to this parquet")
    args = parser.parse_args(argv)

    config = TrainConfig()
    run_dir = new_run_dir()
    progress = Progress(run_dir, config)
    progress.log(f"run folder {run_dir}")
    try:
        progress.log("reading prices, tanker flows, chokepoints and EIA stocks from HF")
        frames = {"prices": data.load_prices(), "flows": data.load_flows(),
                  "chokepoints": data.load_chokepoints(), "stocks": data.load_stocks()}
        progress.state["data"]["coverage"] = _coverage(frames)
        prices = frames["prices"]
        last = prices["price_date"].max()
        assert isinstance(last, date)
        origins = fridays(config.first_origin - timedelta(weeks=60), last)
        progress.log("building features")
        fx = build_features(prices, frames["flows"], frames["chokepoints"], frames["stocks"],
                            origins)
        weekly = fx.filter(pl.col("origin") >= config.first_origin).select("origin", "brent_usd")
        progress.state["prices"] = [[o.isoformat(), round(p, 2)] for o, p in weekly.iter_rows()
                                    if p is not None]
        result = train_and_backtest(fx, config, progress)
        progress.state["status"] = "forecasting from the newest week"
        progress.log("refitting on everything for the newest week")
        progress.state["latest"] = latest_forecast(fx, config)
        progress.state["status"] = "done"
        progress.log("done")
    except Exception as e:
        progress.state["status"] = f"failed: {e}"
        progress.log(f"failed: {e!r}")
        raise
    if args.out:
        result.write_parquet(args.out)
    for r in progress.state["scores"]["all"]:
        print(f"h{r['h']} {r['model']:<20} R² {r['r2']:+.4f}  direction "
              f"{r['hit'] if r['hit'] is not None else '-'}  CW p {r['cw_p']}")


if __name__ == "__main__":
    main()
