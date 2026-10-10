from __future__ import annotations

from datetime import date

import numpy as np
import polars as pl

from src.ml.oil_nowcast.exporters import (
    LAG,
    MODELS,
    backtest,
    build_panel,
    choose,
    scores,
)


def _months(n: int) -> list[date]:
    return pl.date_range(date(2019, 1, 1), date(2040, 1, 1), "1mo", eager=True).head(n).to_list()


def _inputs(n: int = 72, seed: int = 0) -> tuple[pl.DataFrame, pl.DataFrame]:
    """JODI = 7 × the crude terminal's tonnes; a products port adds noise to the total."""
    rng = np.random.default_rng(seed)
    ports, jodi = [], []
    for k, m in enumerate(_months(n)):
        crude = 1e6 * (1 + 0.4 * np.sin(k / 4))
        products = 3e6 * (1 + 0.5 * rng.normal())
        ports += [{"iso3": "NOR", "port_id": "p1", "port_name": "Stura", "month": m,
                   "tonnes": crude},
                  {"iso3": "NOR", "port_id": "p2", "port_name": "Refinery", "month": m,
                   "tonnes": abs(products)},
                  {"iso3": "NOR", "port_id": "p3",
                   "port_name": "Norway - Offshore Oil Terminal 4", "month": m,
                   "tonnes": 0.0}]
        jodi.append({"iso3": "NOR", "month": m, "jodi": 7 * crude})
    return pl.DataFrame(jodi), pl.DataFrame(ports)


def test_panel_splits_terminals_from_other_ports() -> None:
    jodi, ports = _inputs(6)
    p = build_panel(jodi, ports.filter(pl.col("month") != date(2019, 3, 1)), "NOR")
    assert p.months == _months(6)
    assert np.isnan(p.total[2]) and np.isnan(p.terminals[2])  # no complete data that month
    assert np.isclose(p.terminals[0], ports["tonnes"][0])  # Stura + offshore (0)
    assert p.total[0] > p.terminals[0]


def test_terminal_models_see_through_the_products_noise() -> None:
    jodi, ports = _inputs()
    rows = backtest(build_panel(jodi, ports, "NOR"), "NOR")
    med = dict(rows.group_by("model").agg(pl.col("error").median()).iter_rows())
    assert set(med) == set(MODELS)
    assert med["terminals"] < 1e-9 and med["terminal_change"] < 1e-9
    assert med["country"] > 0.1  # the products port swamps the total
    # "last" is JODI from LAG months back.
    first = rows.filter(pl.col("model") == "last").row(0, named=True)
    assert first["month"] == _months(LAG + 1)[LAG]


def test_scores_compare_the_chosen_model_with_last_jodi() -> None:
    jodi, ports = _inputs()
    rows = backtest(build_panel(jodi, ports, "NOR"), "NOR")
    chosen = choose(rows)
    assert chosen["NOR"] in ("terminals", "terminal_change", "ports")
    table = scores(rows, chosen)
    row = table.row(0, named=True)
    assert row["wins"] and row["chosen"] < row["last"] and row["months"] > 0
