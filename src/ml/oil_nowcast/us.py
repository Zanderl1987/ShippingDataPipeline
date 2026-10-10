"""Step 1 of ML4: US monthly seaborne oil trade, about 4 weeks before Census.

Target: Census monthly trade, weight shipped by sea (``vessel_weight_kg``), for crude
(HS 2709) and all oil (2709 crude, 2710 products, 2711 gases), imports and exports.
Census publishes ~5 weeks after the month; PortWatch has the whole month ~1 week
after it. So the nowcast for month M uses Census to M−1 and PortWatch to M.

PortWatch tanker tonnes are summed per coast (Gulf, East, West, other), from the
US ports plus Puerto Rico, the US Virgin Islands and Guam (Census counts them as US).

Every model in ``MODELS`` is scored walk-forward, using only what was public at the
time. For each series, the one with the lowest median error over 2021–2022 is
"chosen" and scored on 2023 on against the baselines (last month, 3-month average).

Run: ``python -m src.ml.oil_nowcast.us`` (reads HF).
"""
from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import polars as pl

#: series -> (Census flow code, HS4 codes)
SERIES: dict[str, tuple[str, tuple[str, ...]]] = {
    "all_oil_exports": ("X", ("2709", "2710", "2711")),
    "all_oil_imports": ("M", ("2709", "2710", "2711")),
    "crude_exports": ("X", ("2709",)),
    "crude_imports": ("M", ("2709",)),
}
COASTS = ("gulf", "east", "west", "other")
TERRITORIES = ("USA", "PRI", "VIR", "GUM")
BASELINES = ("last", "avg3")
CHOOSE_YEARS = (2021, 2022)
TEST_FROM = 2023
#: Months of history the regression is fitted on.
FIT_MONTHS = 24

_COAST_SQL = """CASE
    WHEN p.iso3 <> 'USA' OR p.lat IS NULL OR p.lat > 50 OR p.lon < -130 THEN 'other'
    WHEN p.lon < -110 THEN 'west'
    WHEN p.lat < 31 AND p.lon BETWEEN -98 AND -82 THEN 'gulf'
    ELSE 'east' END"""


# ── Data ──────────────────────────────────────────────────────────────────


def census_sql(trade: str) -> str:
    """Census oil rows summed per month, flow and HS4; ``trade`` is a table or ``read_parquet``."""
    return f"""SELECT period_date AS month, flow_code, left(commodity_code, 4) AS hs4,
                   sum(vessel_weight_kg) / 1000 AS tonnes
            FROM (SELECT * FROM {trade}
                  WHERE left(commodity_code, 4) IN ('2709', '2710', '2711')
                  QUALIFY row_number() OVER (
                      PARTITION BY period_date, flow_code, commodity_code, export_origin
                      ORDER BY ingested_at DESC) = 1)
            GROUP BY ALL"""


def load_census() -> pl.DataFrame:
    """Monthly tonnes shipped by sea per series, from HF: ``month, series, tonnes``."""
    from src.ml.port_forecast.backtest import hf_connection, hf_table

    raw = hf_connection().execute(
        census_sql(f"read_parquet({hf_table('us_trade_products')})")).pl()
    return census_series(raw)


def census_series(raw: pl.DataFrame) -> pl.DataFrame:
    """``month, flow_code, hs4, tonnes`` rows summed into ``SERIES``."""
    return pl.concat([
        raw.filter((pl.col("flow_code") == flow) & pl.col("hs4").is_in(codes))
        .group_by("month").agg(pl.col("tonnes").sum())
        .with_columns(pl.lit(name).alias("series"))
        for name, (flow, codes) in SERIES.items()
    ]).select("month", "series", "tonnes").sort("series", "month")


def load_portwatch() -> pl.DataFrame:
    """Monthly tanker tonnes per coast and direction, from HF (see ``portwatch_sql``)."""
    from src.ml.port_forecast.backtest import hf_connection, hf_table

    return hf_connection().execute(portwatch_sql(
        f"read_parquet({hf_table('port_activity')})",
        f"read_parquet({hf_table('port_profiles')})")).pl()


def portwatch_sql(activity: str, profiles: str) -> str:
    """Monthly tanker tonnes per coast and direction, complete months only.

    ``month, coast, flow ("X"/"M"), tonnes``. Arguments are tables or ``read_parquet``.
    """
    isos = ", ".join(f"'{c}'" for c in TERRITORIES)
    return f"""WITH p AS (
                SELECT port_id, arg_max(latitude, ingested_at) AS lat,
                       arg_max(longitude, ingested_at) AS lon
                FROM {profiles} GROUP BY port_id),
            d AS (
                SELECT date_trunc('month', a.activity_date)::DATE AS month, a.activity_date,
                       {_COAST_SQL.replace('p.iso3', 'a.iso3')} AS coast,
                       a.export_tanker, a.import_tanker
                FROM {activity} a LEFT JOIN p USING (port_id)
                WHERE a.iso3 IN ({isos})),
            m AS (
                SELECT month, coast, count(DISTINCT activity_date) AS days,
                       sum(export_tanker) AS x, sum(import_tanker) AS i
                FROM d GROUP BY ALL)
            SELECT month, coast, 'X' AS flow, x AS tonnes FROM m
            WHERE days = day(last_day(month))
            UNION ALL
            SELECT month, coast, 'M', i FROM m WHERE days = day(last_day(month))
            ORDER BY 1, 2, 3"""


# ── Panel ─────────────────────────────────────────────────────────────────


@dataclass
class Panel:
    """One series on a monthly grid: Census tonnes and PortWatch tonnes per coast."""

    months: list[date]
    census: np.ndarray  # NaN where not published / missing
    coasts: np.ndarray  # (months, len(COASTS)); NaN rows where PortWatch is incomplete

    @property
    def portwatch(self) -> np.ndarray:
        return self.coasts.sum(axis=1)


def build_panel(census: pl.DataFrame, portwatch: pl.DataFrame, series: str) -> Panel:
    flow = SERIES[series][0]
    c = census.filter(pl.col("series") == series)
    pw = (portwatch.filter(pl.col("flow") == flow)
          .pivot(on="coast", index="month", values="tonnes"))
    for k in COASTS:
        if k not in pw.columns:
            pw = pw.with_columns(pl.lit(0.0).alias(k))
    pw = pw.with_columns([pl.col(k).fill_null(0.0) for k in COASTS])
    # Every calendar month from PortWatch's first to its last; incomplete months are NaN.
    first, last = pw["month"].min(), pw["month"].max()
    assert isinstance(first, date) and isinstance(last, date)
    months = pl.date_range(first, last, "1mo", eager=True).to_list()
    cm = dict(c.select("month", "tonnes").iter_rows())
    pm = {r[0]: r[1:] for r in pw.select("month", *COASTS).iter_rows()}
    gap = (math.nan,) * len(COASTS)
    return Panel(
        months=months,
        census=np.array([cm.get(m, math.nan) for m in months], dtype=float),
        coasts=np.array([pm.get(m, gap) for m in months], dtype=float),
    )


# ── Models: nowcast month i from Census before i and PortWatch up to i ───


Model = Callable[[Panel, int], float]


def _last(p: Panel, i: int) -> float:
    return float(p.census[i - 1])


def _avg3(p: Panel, i: int) -> float:
    v = p.census[i - 3:i]
    return float(v.mean()) if i >= 3 and not np.isnan(v).any() else math.nan


def _ratio(n: int) -> Model:
    """PortWatch this month × the median Census/PortWatch ratio of the last n months."""
    def model(p: Panel, i: int) -> float:
        if i < n:
            return math.nan
        r = p.census[i - n:i] / p.portwatch[i - n:i]
        r = r[np.isfinite(r)]
        return float(np.median(r) * p.portwatch[i]) if r.size >= n // 2 + 1 else math.nan
    return model


def _change(p: Panel, i: int) -> float:
    """Last Census month moved by PortWatch's change since then."""
    return float(p.census[i - 1] * p.portwatch[i] / p.portwatch[i - 1])


def _coasts(p: Panel, i: int) -> float:
    """Non-negative least squares of Census on each coast's tonnes, last FIT_MONTHS."""
    from scipy.optimize import nnls

    if i < FIT_MONTHS:
        return math.nan
    x, y = p.coasts[i - FIT_MONTHS:i], p.census[i - FIT_MONTHS:i]
    ok = np.isfinite(y) & np.isfinite(x).all(axis=1)
    if ok.sum() < FIT_MONTHS // 2:
        return math.nan
    b, _ = nnls(x[ok], y[ok])
    return float(p.coasts[i] @ b)


def _blend(p: Panel, i: int) -> float:
    """Average of the 12-month ratio nowcast and the 3-month average."""
    return float(np.mean([_ratio(12)(p, i), _avg3(p, i)]))


MODELS: dict[str, Model] = {
    "last": _last,
    "avg3": _avg3,
    "ratio12": _ratio(12),
    "ratio3": _ratio(3),
    "change": _change,
    "coasts": _coasts,
    "blend": _blend,
}


def backtest(p: Panel, series: str) -> pl.DataFrame:
    """Every model's nowcast for every month with a Census figure to check against."""
    rows = []
    for i in range(1, len(p.months)):
        actual = p.census[i]
        if not np.isfinite(actual) or not np.isfinite(p.coasts[i]).all():
            continue
        for name, model in MODELS.items():
            with np.errstate(all="ignore"):
                guess = model(p, i)
            if np.isfinite(guess):
                rows.append({"series": series, "month": p.months[i], "model": name,
                             "nowcast": guess, "actual": float(actual),
                             "error": abs(guess / actual - 1)})
    return pl.DataFrame(rows)


def choose(rows: pl.DataFrame) -> dict[str, str]:
    """Per series, the non-baseline model with the lowest median error in CHOOSE_YEARS."""
    picked = (
        rows.filter(pl.col("month").dt.year().is_in(CHOOSE_YEARS)
                    & ~pl.col("model").is_in(BASELINES))
        .group_by("series", "model").agg(pl.col("error").median())
        .sort("series", "error")
        .group_by("series", maintain_order=True).first()
    )
    return dict(picked.select("series", "model").iter_rows())


def scores(rows: pl.DataFrame, chosen: dict[str, str]) -> pl.DataFrame:
    """Median % error per series, test year and model, plus the target check.

    ``chosen`` is the chosen model; ``best_baseline`` the better of last/avg3 that year.
    """
    test = rows.filter(pl.col("month").dt.year() >= TEST_FROM).with_columns(
        pl.col("month").dt.year().cast(pl.Utf8).alias("year"))
    both = pl.concat([test, test.with_columns(pl.lit("2023+").alias("year"))])
    med = (both.group_by("series", "year", "model")
           .agg((pl.col("error").median() * 100).alias("pct"), pl.len().alias("months"))
           .pivot(on="model", index=["series", "year", "months"], values="pct"))
    return (
        med.with_columns(
            pl.col("series").replace_strict(chosen, default=None).alias("model"),
            pl.min_horizontal(*BASELINES).alias("best_baseline"),
        )
        .with_columns(
            pl.struct(pl.all()).map_elements(lambda r: r[r["model"]], return_dtype=pl.Float64)
            .alias("chosen"),
        )
        .with_columns((pl.col("chosen") / pl.col("best_baseline")).alias("vs_baseline"))
        .sort("series", "year")
    )


def target_met(table: pl.DataFrame) -> dict[str, bool]:
    """Per series: below the best baseline every year, and ≤ 75% of it over 2023+."""
    out = {}
    for (s,), g in table.group_by("series"):
        years = g.filter(pl.col("year") != "2023+")
        overall = g.filter(pl.col("year") == "2023+")["vs_baseline"][0]
        out[str(s)] = bool((years["vs_baseline"] < 1).all() and overall <= 0.75)
    return out


# ── CLI ───────────────────────────────────────────────────────────────────


def _fmt(v: object) -> str:
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return "–"
    return f"{v:.2f}" if isinstance(v, float) and abs(v) < 2 else (
        f"{v:.1f}" if isinstance(v, float) else str(v))


def markdown(df: pl.DataFrame) -> str:
    cols = df.columns
    lines = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(_fmt(r[c]) for c in cols) + " |" for r in df.iter_rows(named=True)]
    return "\n".join(lines)


def run(census: pl.DataFrame, portwatch: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = pl.concat([backtest(build_panel(census, portwatch, s), s) for s in SERIES])
    table = scores(rows, choose(rows))
    return rows, table


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backtest the US seaborne oil trade nowcast")
    parser.add_argument("--csv", type=Path, help="also write the nowcasts here")
    args = parser.parse_args(argv)

    rows, table = run(load_census(), load_portwatch())
    print(markdown(table.select("series", "year", "months", *MODELS, "model", "chosen",
                                "best_baseline", "vs_baseline")))
    print()
    for s, ok in sorted(target_met(table).items()):
        print(f"{s}: target {'met' if ok else 'NOT met'}")
    if args.csv:
        args.csv.parent.mkdir(parents=True, exist_ok=True)
        rows.write_csv(args.csv)


if __name__ == "__main__":
    main()
