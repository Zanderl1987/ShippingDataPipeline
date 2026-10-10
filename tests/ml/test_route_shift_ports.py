from __future__ import annotations

import math

import numpy as np
import polars as pl

from src.ml.route_shift.ports import (
    METRICS,
    changes,
    label_ports,
    run,
    weekly_levels,
)

YEARS = (2021, 2022, 2023, 2024, 2025)
WILD = {2021: 1.0, 2022: 0.3, 2023: 1.0, 2024: 0.6, 2025: 1.0}
COLS = sorted({c for cs, _ in METRICS.values() for c in cs})


def _windows(n: int = 60, seed: int = 0, shock: dict[str, float] | None = None,
             wild: tuple[str, ...] = ()) -> pl.DataFrame:
    """Feb–Jul sums for n ports, ~5% noise a year.

    ``shock`` multiplies a port's 2024 and later (a lasting change, like the Red Sea's).
    ``wild`` ports swing by up to 70% every year.
    """
    rng = np.random.default_rng(seed)
    rows = []
    for k in range(n):
        pid = f"p{k}"
        level = 30 + 10 * k  # calls a week
        for y in YEARS:
            f = math.exp(rng.normal(0, 0.05))
            if pid in wild:
                f *= WILD[y]
            if y >= 2024 and shock and pid in shock:
                f *= shock[pid]
            weekly = level * f
            row = {"port_id": pid, "year": y, "days": 181, "port_name": f"Port {k}",
                   "iso3": "AAA", "continent": "Europe", "latitude": 50.0, "longitude": 0.0}
            for c in COLS:
                row[c] = weekly * 181 / 7 * (1000 if "port" not in c else 1)
            rows.append(row)
    return pl.DataFrame(rows)


def test_changes_remove_the_band_median_and_skip_small_ports() -> None:
    w = _windows(10).with_columns(
        pl.when(pl.col("port_id") == "p0").then(pl.lit(100.0)).otherwise(pl.col("portcalls"))
        .alias("portcalls"))  # p0 ~4 calls a week: too small
    w = w.with_columns(pl.when(pl.col("year") == 2024).then(pl.col(c) * 1.2)
                       .otherwise(pl.col(c)).alias(c) for c in COLS)  # world grows 20%
    out = changes(weekly_levels(w), [(2023, 2024)]).filter(pl.col("metric") == "calls")
    assert "p0" not in out["port_id"].to_list()
    assert out["effect"].abs().max() < 0.2  # the 20% world rise is removed
    assert set(out["band"]) <= {"small", "medium", "large"}


def test_part_reported_windows_are_dropped() -> None:
    w = _windows(5).with_columns(
        pl.when((pl.col("port_id") == "p1") & (pl.col("year") == 2024)).then(100)
        .otherwise(pl.col("days")).alias("days"))
    lv = weekly_levels(w)
    assert lv.filter((pl.col("port_id") == "p1") & (pl.col("year") == 2024)).is_empty()


def test_run_flags_a_real_loss_but_not_a_port_that_always_swings() -> None:
    w = _windows(80, shock={"p3": 0.3}, wild=("p7",))
    out = run(w)
    ports = out["ports"].filter(pl.col("metric") == "calls")
    flagged = set(ports.filter("flagged")["port_id"])
    assert "p3" in flagged
    p7 = ports.filter(pl.col("port_id") == "p7").row(0, named=True)
    assert p7["outside_band"] and not p7["flagged"]  # -40%, but it moved 70% in 2022
    counts = out["counts"].filter(pl.col("metric") == "calls").row(0, named=True)
    assert counts["lost"] >= 1 and counts["ports"] == 80
    assert abs(counts["expected_by_chance"] - 4) < 1e-9


def test_group_effects_compare_with_each_placebo_pair() -> None:
    names = {"p20": "Singapore", "p21": "Rotterdam"}  # both in the large band
    w = _windows(40, shock={"p20": 0.5, "p21": 0.5}).with_columns(
        pl.col("port_id").replace_strict(names, default=None).fill_null(pl.col("port_name"))
        .alias("port_name"))
    g = run(w)["groups"].filter((pl.col("group") == "Asia–Europe hubs")
                                & (pl.col("metric") == "calls")).row(0, named=True)
    assert g["ports"] == 2 and g["mean"] < -0.5 and g["z"] < -3
    assert {"2021-2022", "2022-2023", "2024-2025"} <= set(g)


def test_label_ports_marks_red_sea_and_confounders() -> None:
    ports = pl.DataFrame({
        "port_name": ["Jeddah", "Port Said", "Haifa", "Baltimore", "Rotterdam"],
        "iso3": ["SAU", "EGY", "ISR", "USA", "NLD"],
        "continent": ["Asia & Pacific", "Africa", "Asia & Pacific", "North America", "Europe"],
        "latitude": [21.5, 31.26, 32.8, 39.3, 51.9],
        "longitude": [39.2, 32.3, 35.0, -76.6, 4.0],
    })
    out = label_ports(ports)
    assert out["group"].to_list() == ["Red Sea", "Africa", "Asia & Pacific", "North America",
                                      "Europe"]
    assert out["confounder"].to_list()[2:] == ["Israel–Gaza war",
                                                "Key Bridge collapse, 2024-03-26", None]
