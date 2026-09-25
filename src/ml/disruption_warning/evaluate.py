"""Scoring disruption warnings.

Disruptions are rare (under 1% of port-weeks), so accuracy and ROC-AUC would
flatter anything. Per model, over the same rows:

- ``ap``: average precision, the area under the precision-recall curve. A
  model that ranks at random scores the base rate (``rate``); 1 is perfect.
  Tied scores are ranked as one block, so a 0/1 flag gets no credit for luck.
- ``precision`` / ``recall`` at an alert budget: each week, for each horizon,
  flag the top ``budget`` share of ports (1% of ~900 ports: ~9 alerts a week).
- ``big_recall``: the share of 50%+ disruptions inside that budget.
"""
from __future__ import annotations

import math

import polars as pl

BUDGET = 0.01


def average_precision(rows: pl.DataFrame, model: str, target: str = "y") -> float | None:
    by_score = (
        rows.group_by(model)
        .agg(pl.col(target).cast(pl.Int64).sum().alias("tp"), pl.len().alias("n"))
        .sort(model, descending=True)
        .with_columns(
            pl.col("tp").cum_sum().alias("cum_tp"), pl.col("n").cum_sum().alias("cum_n")
        )
    )
    positives = int(by_score["tp"].sum())
    if not positives:
        return None
    return float((by_score["tp"] * by_score["cum_tp"] / by_score["cum_n"]).sum()) / positives


def _alerts(model: str, budget: float) -> pl.Expr:
    """True for the top ``budget`` share of ports per origin and horizon. Rows
    must be sorted by port_id: ties at the cut then go to the first ports, so
    the number of alerts is exact."""
    group = ["origin_week", "horizon"]
    k = (pl.len().over(group) * budget).ceil()
    return pl.col(model).rank("ordinal", descending=True).over(group) <= k


def score(
    rows: pl.DataFrame,
    models: list[str],
    by: list[str] | None = None,
    budget: float = BUDGET,
) -> pl.DataFrame:
    """One row per group in ``by`` and model: n, rate, ap, precision, recall,
    big_recall."""
    alerts = [_alerts(m, budget).alias(f"_alert_{m}") for m in models]
    rows = rows.sort("port_id").with_columns(alerts)
    groups = rows.partition_by(by, as_dict=True, maintain_order=True) if by else {(): rows}
    out = []
    for key, part in groups.items():
        positives = int(part["y"].sum())
        big = int(part["big"].sum())
        for m in models:
            alert = part[f"_alert_{m}"]
            hits = int((alert & part["y"]).sum())
            flagged = int(alert.sum())
            out.append(
                {
                    **dict(zip(by or [], key, strict=True)),
                    "model": m,
                    "n": part.height,
                    "rate": positives / part.height,
                    "ap": average_precision(part, m),
                    "precision": hits / flagged if flagged else None,
                    "recall": hits / positives if positives else None,
                    "big_recall": int((alert & part["big"]).sum()) / big if big else None,
                }
            )
    return pl.DataFrame(out)


def flag_stats(rows: pl.DataFrame, flag_rules: list[str]) -> pl.DataFrame:
    """For 0/1 rules, the flag itself (score >= 1): alerts per week, precision
    and recall."""
    weeks = rows.select("origin_week", "horizon").unique().height
    positives = int(rows["y"].sum())
    out = []
    for m in flag_rules:
        flag = rows[m] >= 1
        hits = int((flag & rows["y"]).sum())
        flagged = int(flag.sum())
        out.append(
            {
                "rule": m,
                "alerts_per_week": flagged / weeks if weeks else math.nan,
                "precision": hits / flagged if flagged else None,
                "recall": hits / positives if positives else None,
            }
        )
    return pl.DataFrame(out)
