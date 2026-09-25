"""Scoring forecasts of weekly port calls.

Two numbers per forecaster, always over the same rows (those where every
forecaster being compared has a forecast), so no one is scored on easier weeks:

- ``wape_<m>``: total absolute error as a share of total actual calls. Busy
  ports weigh more, as they do in real traffic.
- ``vs_ref_<m>``: total absolute error divided by that of a reference forecast.
  Below 1 beats it; 0.9 is 10% less error.

The default reference is ``last_4`` (average of the last 4 known weeks): on
2023-2026 it had ~30% less error than "same week last year", so it is the bar.
"""
from __future__ import annotations

import polars as pl
import polars.selectors as cs

REFERENCE = "last_4"


def score(
    forecasts: pl.DataFrame,
    models: list[str],
    by: list[str] | None = None,
    reference: str = REFERENCE,
) -> pl.DataFrame:
    """One row per group in ``by`` (default: horizon), one ``wape_<m>`` and one
    ``vs_ref_<m>`` column per model, plus ``n`` and total actual calls."""
    by = by or ["horizon"]
    names = list(dict.fromkeys([*models, reference]))
    common = forecasts.drop_nulls([*names, "actual"])
    aggs: list[pl.Expr] = [pl.len().alias("n"), pl.col("actual").sum().alias("calls")]
    for m in names:
        aggs.append((pl.col(m) - pl.col("actual")).abs().sum().alias(f"_ae_{m}"))
    out = common.group_by(by).agg(aggs)
    cols: list[pl.Expr] = []
    for m in models:
        ae = pl.col(f"_ae_{m}")
        cols.append((ae / pl.col("calls")).alias(f"wape_{m}"))
        cols.append((ae / pl.col(f"_ae_{reference}")).alias(f"vs_ref_{m}"))
    return out.with_columns(cols).drop(cs.starts_with("_ae_")).sort(by)
