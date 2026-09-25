"""One row per Friday: what was known that day, and Brent's change afterwards.

Release lags (so the backtest only uses what was public on the Friday):

- Brent / WTI: that day's close.
- PortWatch tanker flows: published Tuesday, through the previous Friday.
- PortWatch chokepoints: published the same Tuesday, through Sunday; we use
  the same Friday-ending week to be safe.
- EIA weekly stocks: week ending Friday, published the next Wednesday.

So every non-price input is the week that ended 7 days before the origin.
Known gap: PortWatch history is today's vintage, not what was published at
the time (revisions are small but not zero; TASKS ML1a).
"""
from __future__ import annotations

from datetime import date, timedelta

import polars as pl

HORIZONS = (1, 2, 4)
#: Weeks between an origin and the newest non-price week it may use.
RELEASE_LAG_WEEKS = 1

PRICE_FEATURES = ["r1", "r4", "r13", "r52", "vol4", "spread", "spread_chg4", "dd52"]
STOCK_NAMES = ["crude", "cushing", "gasoline", "distillate"]
STOCK_FEATURES = [
    *[f"stock_{s}_{k}" for s in STOCK_NAMES for k in ("chg1", "chg4", "dev5y")],
    "stock_spr_chg4",
]
SHIP_PREFIXES = ("exp_", "imp_", "choke_")


def fridays(start: date, end: date) -> list[date]:
    first = start + timedelta(days=(4 - start.weekday()) % 7)
    return [first + timedelta(weeks=k) for k in range((end - first).days // 7 + 1)]


def _asof(prices: pl.DataFrame, days: list[date], col: str = "brent_usd") -> pl.DataFrame:
    """The last close on or before each day."""
    return (
        pl.DataFrame({"day": days}).sort("day")
        .join_asof(prices.select("price_date", col).sort("price_date"),
                   left_on="day", right_on="price_date", strategy="backward")
        .select("day", col)
    )


def price_features(prices: pl.DataFrame, origins: list[date]) -> pl.DataFrame:
    import numpy as np

    origins = sorted(origins)
    p = prices.sort("price_date").with_columns(
        pl.col("brent_usd").log().diff().alias("ret"),
        (pl.col("brent_usd") - pl.col("wti_usd")).alias("spread"),
    )
    p = p.with_columns(
        (pl.col("ret").rolling_std(20) * np.sqrt(252)).alias("vol4"),
        pl.col("brent_usd").rolling_max(260, min_samples=200).alias("high52"),
    )
    out = pl.DataFrame({"origin": origins}).sort("origin")
    out = out.join_asof(
        p.select("price_date", "brent_usd", "vol4", "spread", "high52"),
        left_on="origin", right_on="price_date", strategy="backward",
    ).drop("price_date")
    for k, weeks in (("r1", 1), ("r4", 4), ("r13", 13), ("r52", 52)):
        back = _asof(p, [o - timedelta(weeks=weeks) for o in origins]).rename(
            {"brent_usd": "back"})
        out = out.with_columns(
            (pl.col("brent_usd") / back.sort("day")["back"]).log().alias(k)
        )
    spread4 = _asof(p, [o - timedelta(weeks=4) for o in origins], "spread")
    return out.with_columns(
        (pl.col("spread") - spread4.sort("day")["spread"]).alias("spread_chg4"),
        (pl.col("brent_usd") / pl.col("high52")).log().alias("dd52"),
    ).drop("high52")


def targets(prices: pl.DataFrame, origins: list[date]) -> pl.DataFrame:
    """``y{h}`` = log change of Brent from the origin's close to h weeks later.

    Null when that day hasn't come yet.
    """
    last = prices["price_date"].max()
    assert isinstance(last, date)
    origins = sorted(origins)
    now = _asof(prices, origins)
    out = pl.DataFrame({"origin": origins})
    for h in HORIZONS:
        later = [o + timedelta(weeks=h) for o in origins]
        future = _asof(prices, later)
        ratio = (future["brent_usd"] / now["brent_usd"]).log()
        known = pl.Series([d <= last for d in later])
        out = out.with_columns(pl.when(known).then(ratio).otherwise(None).alias(f"y{h}"))
    return out


def weekly_wide(long: pl.DataFrame, full_days: int | None = 7) -> pl.DataFrame:
    """Long weekly series -> one column per series; part weeks become null."""
    if full_days is not None and "days" in long.columns:
        long = long.with_columns(
            pl.when(pl.col("days") >= full_days).then(pl.col("value")).alias("value"))
    wide = long.pivot(on="series", index="week_end", values="value", aggregate_function="first")
    # A regular Friday grid, so shifts below count weeks, not rows.
    first, last = wide["week_end"].min(), wide["week_end"].max()
    assert isinstance(first, date) and isinstance(last, date)
    grid = pl.DataFrame({"week_end": fridays(first, last)})
    return grid.join(wide, on="week_end", how="left").sort("week_end")


def flow_features(wide: pl.DataFrame) -> pl.DataFrame:
    """Per series: last week vs the 4 before (``_w4``), and the last 4 weeks vs
    the same 4 a year earlier (``_yoy``), both as log ratios."""
    cols = [c for c in wide.columns if c != "week_end"]
    exprs = []
    for c in cols:
        x = pl.col(c)
        prior4 = pl.mean_horizontal([x.shift(k) for k in range(1, 5)])
        last4 = pl.mean_horizontal([x.shift(k) for k in range(4)])
        year4 = pl.mean_horizontal([x.shift(k) for k in range(52, 56)])
        exprs += [
            (x / prior4).log().alias(f"{c}_w4"),
            (last4 / year4).log().alias(f"{c}_yoy"),
        ]
    out = wide.select("week_end", *exprs)
    # log(0) or 0/0 -> not a number; treat as missing.
    return out.with_columns(
        pl.when(pl.col(c).is_finite()).then(pl.col(c)).alias(c)
        for c in out.columns if c != "week_end"
    )


def stock_features(wide: pl.DataFrame) -> pl.DataFrame:
    """Weekly and 4-week changes (% of level) and the gap to the 5-year
    average for the same week, the numbers traders quote."""
    exprs = []
    for s in STOCK_NAMES:
        x = pl.col(f"stock_{s}")
        five = pl.mean_horizontal([x.shift(52 * k) for k in range(1, 6)])
        exprs += [
            (x / x.shift(1) - 1).alias(f"stock_{s}_chg1"),
            (x / x.shift(4) - 1).alias(f"stock_{s}_chg4"),
            (x / five - 1).alias(f"stock_{s}_dev5y"),
        ]
    exprs.append((pl.col("stock_spr") / pl.col("stock_spr").shift(4) - 1).alias("stock_spr_chg4"))
    return wide.select("week_end", *exprs)


def build_features(
    prices: pl.DataFrame,
    flows: pl.DataFrame,
    chokepoints: pl.DataFrame,
    stocks: pl.DataFrame,
    origins: list[date],
) -> pl.DataFrame:
    """Features and targets per origin Friday."""
    ship = flow_features(weekly_wide(pl.concat([flows, chokepoints], how="diagonal_relaxed")))
    stk = stock_features(weekly_wide(stocks, full_days=None))
    lag = timedelta(weeks=RELEASE_LAG_WEEKS)
    base = pl.DataFrame({"origin": sorted(origins)}).with_columns(
        (pl.col("origin") - lag).alias("week_end"))
    out = (
        base.join(price_features(prices, origins), on="origin", how="left")
        .join(ship, on="week_end", how="left")
        .join(stk, on="week_end", how="left")
        .join(targets(prices, origins), on="origin", how="left")
    )
    return out.sort("origin")


def feature_sets(fx: pl.DataFrame) -> dict[str, list[str]]:
    """The nested input sets: adding stocks, then shipping, to price alone."""
    ship = [c for c in fx.columns if c.startswith(SHIP_PREFIXES)]
    return {
        "price": PRICE_FEATURES,
        "price_stocks": [*PRICE_FEATURES, *STOCK_FEATURES],
        "full": [*PRICE_FEATURES, *STOCK_FEATURES, *ship],
    }
