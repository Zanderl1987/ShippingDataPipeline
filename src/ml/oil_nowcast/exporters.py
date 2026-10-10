"""Step 2 of ML4: other countries' crude exports (JODI), about 2 months early.

JODI publishes a month's crude exports ~10 weeks later, so when PortWatch has month M
complete, JODI's newest month is M−2. Step 0 found that a country's total PortWatch
tanker tonnes mostly lose to "last JODI" (products, chemicals and gas swamp the
crude). This step tries only each country's crude export terminals, listed in
``TERMINALS`` from what the terminals are known for, before looking at any scores.

Models are chosen per country on 2021–2022 and scored from 2023 against "last JODI".
The step succeeds if the chosen model beats it for at least ``MIN_WINS`` countries.

Result (2026-10-10): it doesn't. The chosen model beats "last JODI" for 2 of 9
countries (Algeria, Norway); the best model in hindsight for 4. As a "big move"
flag (terminals say 20%+), it fires in a third of country-months and is right
(10%+ the same way) only ~40% of the time. Kept as a documented negative result.

Run: ``python -m src.ml.oil_nowcast.exporters`` (reads HF).
"""
from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date

import numpy as np
import polars as pl

from src.ml.oil_nowcast.us import markdown

#: ISO3 -> (JODI two-letter code, crude export terminals by PortWatch port name).
#: Every country's offshore loading points ("... - Offshore Oil Terminal N") count too.
TERMINALS: dict[str, tuple[str, tuple[str, ...]]] = {
    "SAU": ("SA", ("Juaymah", "Ras Tanura", "Yanbu (King Fahd Port)")),
    "USA": ("US", ("Corpus Christi", "Port Aransas", "Houston (US-TX)", "Beaumont")),
    "IRQ": ("IQ", ("Basrah Oil Terminal", "Al Basrah")),
    "CAN": ("CA", ("Whiffen Head", "Vancouver")),
    "NOR": ("NO", ("Mongstad", "Stura")),
    "KWT": ("KW", ("Mina Al Ahmadi",)),
    "NGA": ("NG", ("Bonny", "Escravos (Oil Terminal)")),
    "GBR": ("GB", ("Hound Point", "Sullom Voe", "Flotta")),
    "DZA": ("DZ", ("Arzew", "Bejaia")),
}
OFFSHORE = "Offshore Oil Terminal"
LAG = 2  # JODI's newest month when PortWatch has month M
CHOOSE_YEARS = (2021, 2022)
TEST_FROM = 2023
MIN_WINS = 5
FIT_MONTHS = 36


# ── Data ──────────────────────────────────────────────────────────────────


def load_jodi() -> pl.DataFrame:
    """Monthly crude exports (thousand barrels) per country: ``iso3, month, jodi``."""
    from src.ml.port_forecast.backtest import hf_connection, hf_table

    codes = {v[0]: k for k, v in TERMINALS.items()}
    raw = hf_connection().execute(
        f"""SELECT period, reporting_country, quantity_barrels
            FROM read_parquet({hf_table('oil_trade')})
            WHERE product = 'Crude oil' AND flow = 'Exports' AND unit = 'KBBL'
            QUALIFY row_number() OVER (PARTITION BY period, reporting_country
                                       ORDER BY ingested_at DESC) = 1"""
    ).pl()
    return (raw.filter(pl.col("reporting_country").is_in(list(codes)))
            .select(pl.col("reporting_country").replace_strict(codes).alias("iso3"),
                    (pl.col("period") + "-01").str.to_date().alias("month"),
                    pl.col("quantity_barrels").alias("jodi")))


def load_ports() -> pl.DataFrame:
    """Monthly tanker export tonnes per port, complete months only.

    ``iso3, port_id, port_name, month, tonnes``.
    """
    from src.ml.port_forecast.backtest import hf_connection, hf_table

    isos = ", ".join(f"'{k}'" for k in TERMINALS)
    return hf_connection().execute(
        f"""SELECT iso3, port_id, arg_max(port_name, activity_date) AS port_name,
                   date_trunc('month', activity_date)::DATE AS month,
                   sum(export_tanker) AS tonnes, count(DISTINCT activity_date) AS days
            FROM read_parquet({hf_table('port_activity')}) WHERE iso3 IN ({isos})
            GROUP BY iso3, port_id, month
            HAVING count(DISTINCT activity_date) = day(last_day(month))"""
    ).pl().drop("days")


def is_terminal(iso3: str) -> pl.Expr:
    names = TERMINALS[iso3][1]
    return pl.col("port_name").is_in(names) | pl.col("port_name").str.contains(OFFSHORE)


# ── Panel and models ──────────────────────────────────────────────────────


@dataclass
class Panel:
    months: list[date]
    jodi: np.ndarray  # NaN where missing
    total: np.ndarray  # all the country's tanker export tonnes
    terminals: np.ndarray  # the crude terminals only
    ports: np.ndarray  # (months, top ports by volume), for the regression


def build_panel(jodi: pl.DataFrame, ports: pl.DataFrame, iso3: str, top: int = 8) -> Panel:
    p = ports.filter(pl.col("iso3") == iso3)
    first, last = p["month"].min(), p["month"].max()
    assert isinstance(first, date) and isinstance(last, date)
    months = pl.date_range(first, last, "1mo", eager=True).to_list()
    complete = p.group_by("month").len()  # months where any port reported
    have = set(complete["month"])
    big = (p.filter(pl.col("month") < date(2021, 1, 1)).group_by("port_id")
           .agg(pl.col("tonnes").sum()).sort("tonnes", descending=True).head(top)["port_id"]
           .to_list())

    def by_month(frame: pl.DataFrame) -> dict[date, float]:
        return dict(frame.group_by("month").agg(pl.col("tonnes").sum()).iter_rows())

    tot, term = by_month(p), by_month(p.filter(is_terminal(iso3)))
    wide = (p.filter(pl.col("port_id").is_in(big)).pivot(on="port_id", index="month",
                                                          values="tonnes").fill_null(0.0))
    cols = [c for c in wide.columns if c != "month"]
    pm = {r[0]: r[1:] for r in wide.select("month", *cols).iter_rows()}
    jm = dict(jodi.filter(pl.col("iso3") == iso3).select("month", "jodi").iter_rows())
    nan = math.nan

    def arr(d: dict[date, float]) -> np.ndarray:
        return np.array([d.get(m, 0.0) if m in have else nan for m in months], dtype=float)

    return Panel(
        months=months,
        jodi=np.array([jm.get(m, nan) for m in months], dtype=float),
        total=arr(tot), terminals=arr(term),
        ports=np.array([pm.get(m, (0.0,) * len(cols)) if m in have else (nan,) * len(cols)
                        for m in months], dtype=float).reshape(len(months), len(cols)),
    )


Model = Callable[[Panel, int], float]


def _last(p: Panel, i: int) -> float:
    return float(p.jodi[i - LAG])


def _ratio(series: str) -> Model:
    """This month's tonnes × the median JODI/tonnes ratio of the 12 newest JODI months."""
    def model(p: Panel, i: int) -> float:
        x = getattr(p, series)
        lo, hi = i - LAG - 11, i - LAG + 1
        if lo < 0:
            return math.nan
        r = p.jodi[lo:hi] / x[lo:hi]
        r = r[np.isfinite(r) & (r > 0)]
        return float(np.median(r) * x[i]) if r.size >= 6 else math.nan
    return model


def _change(p: Panel, i: int) -> float:
    """Newest JODI month moved by the terminals' change since then."""
    return float(p.jodi[i - LAG] * p.terminals[i] / p.terminals[i - LAG])


def _ports(p: Panel, i: int) -> float:
    """Non-negative least squares on the biggest ports over the newest FIT_MONTHS of JODI."""
    from scipy.optimize import nnls

    lo, hi = i - LAG - FIT_MONTHS + 1, i - LAG + 1
    if lo < 0 or p.ports.shape[1] == 0:
        return math.nan
    x, y = p.ports[lo:hi], p.jodi[lo:hi]
    ok = np.isfinite(y) & np.isfinite(x).all(axis=1)
    if ok.sum() < FIT_MONTHS // 2:
        return math.nan
    b, _ = nnls(x[ok], y[ok])
    return float(p.ports[i] @ b)


MODELS: dict[str, Model] = {
    "last": _last,
    "country": _ratio("total"),
    "terminals": _ratio("terminals"),
    "terminal_change": _change,
    "ports": _ports,
}


def backtest(p: Panel, iso3: str) -> pl.DataFrame:
    rows = []
    for i in range(LAG, len(p.months)):
        actual = p.jodi[i]
        if not (np.isfinite(actual) and actual > 0 and np.isfinite(p.total[i])):
            continue
        for name, model in MODELS.items():
            with np.errstate(all="ignore"):
                guess = model(p, i)
            if np.isfinite(guess):
                rows.append({"iso3": iso3, "month": p.months[i], "model": name,
                             "nowcast": guess, "actual": float(actual),
                             "error": abs(guess / actual - 1)})
    return pl.DataFrame(rows)


def choose(rows: pl.DataFrame) -> dict[str, str]:
    early = pl.col("month").dt.year().is_in(CHOOSE_YEARS)
    picked = (rows.filter(early & (pl.col("model") != "last"))
              .group_by("iso3", "model").agg(pl.col("error").median())
              .sort("iso3", "error").group_by("iso3", maintain_order=True).first())
    return dict(picked.select("iso3", "model").iter_rows())


def scores(rows: pl.DataFrame, chosen: dict[str, str]) -> pl.DataFrame:
    """Median % error from TEST_FROM per country and model; ``chosen`` vs ``last``."""
    test = rows.filter(pl.col("month").dt.year() >= TEST_FROM)
    med = (test.group_by("iso3", "model").agg((pl.col("error").median() * 100).alias("pct"))
           .pivot(on="model", index="iso3", values="pct"))
    months = test.filter(pl.col("model") == "last").group_by("iso3").agg(pl.len().alias("months"))
    return (
        med.join(months, on="iso3")
        .with_columns(pl.col("iso3").replace_strict(chosen, default=None).alias("model"))
        .with_columns(pl.struct(pl.all()).map_elements(
            lambda r: r[r["model"]] if r["model"] else None, return_dtype=pl.Float64)
            .alias("chosen"))
        .with_columns((pl.col("chosen") < pl.col("last")).alias("wins"))
        .sort("iso3")
    )


def run(jodi: pl.DataFrame, ports: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = pl.concat([backtest(build_panel(jodi, ports, k), k) for k in TERMINALS])
    return rows, scores(rows, choose(rows))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Backtest crude export nowcasts against JODI")
    parser.parse_args(argv)
    _, table = run(load_jodi(), load_ports())
    print(markdown(table.select("iso3", "months", *MODELS, "model", "chosen", "wins")))
    wins = int(table["wins"].sum())
    print(f"\nchosen model beats last JODI for {wins} of {table.height} countries "
          f"(target {MIN_WINS}): {'met' if wins >= MIN_WINS else 'NOT met'}")


if __name__ == "__main__":
    main()
