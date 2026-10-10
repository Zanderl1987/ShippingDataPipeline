"""Step 2 of ML3: which ports gained or lost traffic after the Red Sea attacks.

Each port's change is year on year over the same months (February–July), so a
port's own seasons cancel: 2024 against 2023 for the shock, all before the first
attack (2023-12-18) and from 7 weeks after it, when most ships had rerouted
(see ``episodes``). Each change is measured against the median change of ports
of the same size that year, which removes world trends.

Normal noise comes from three placebo year pairs with no new Red Sea shock in
between: 2021→22, 2022→23 and 2024→25 (both halves after the switch). A port is
flagged only if its change is outside the 2.5–97.5% range of placebo changes for
its size band AND bigger than its own largest placebo change, so ports that swing
every year are not called gainers or losers. With 5% of ports outside the band
range by chance, the number flagged is compared with that.

Run: ``python -m src.ml.route_shift.ports [--csv DIR]`` (reads HF).
"""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

import polars as pl

from src.ml.route_shift.episodes import markdown

MONTHS = (2, 7)  # February to July
SHOCK = (2023, 2024)
PLACEBOS = ((2021, 2022), (2022, 2023), (2024, 2025))
#: metric -> (columns summed, the size metric whose weekly level sets the bands)
METRICS: dict[str, tuple[tuple[str, ...], str]] = {
    "calls": (("portcalls",), "calls"),
    "container_calls": (("portcalls_container",), "container_calls"),
    "container_tonnes": (("import_container", "export_container"), "container_calls"),
    "tonnes": (("import_total", "export_total"), "calls"),
}
#: Smallest weekly level (in the size metric, before the change) that is measured.
MIN_WEEKLY = {"calls": 20.0, "container_calls": 10.0}
#: Size bands, in multiples of MIN_WEEKLY: 1–2.5x, 2.5–7.5x, 7.5x+.
BANDS = (2.5, 7.5)
TAIL = 0.025

#: Red Sea and Gulf of Aden: (lat_min, lat_max, lon_min, lon_max). Port Said and
#: the Mediterranean side of Egypt are north of 30.5°.
RED_SEA_BOX = (10.0, 30.5, 32.0, 46.0)
#: Container hubs on Asia–Europe services (listed before measuring them). If ships
#: on a ~10-day longer loop call less often, these should lose container calls.
ASIA_EUROPE_HUBS = (
    "Shanghai", "Ningbo", "Yantian", "Hong Kong", "Busan", "Singapore", "Tanjung Pelepas",
    "Port Klang", "Colombo", "Salalah", "Piraeus", "Gioia Tauro", "Valencia", "Algeciras",
    "Tangier-Mediterranean", "Rotterdam", "Antwerp", "Hamburg", "Felixstowe",
)
#: Known non-Red Sea shocks in the same window, by ISO3 or port name.
CONFOUNDED: dict[str, str] = {
    "ISR": "Israel–Gaza war",
    "RUS": "sanctions on Russia",
    "UKR": "war in Ukraine",
    "Baltimore": "Key Bridge collapse, 2024-03-26",
    "Balboa": "Panama drought",
    "Cristobal": "Panama drought",
    "Colon": "Panama drought",
    "Manzanillo (Panama)": "Panama drought",
}


def load_windows() -> pl.DataFrame:
    """Per port and year: days reported and the summed columns for Feb–Jul."""
    from src.ml.port_forecast.backtest import hf_connection, hf_table

    cols = sorted({c for cs, _ in METRICS.values() for c in cs})
    sums = ", ".join(f"sum({c})::DOUBLE AS {c}" for c in cols)
    years = sorted({y for pair in (SHOCK, *PLACEBOS) for y in pair})
    conn = hf_connection()
    windows = conn.execute(
        f"""SELECT port_id, year(activity_date) AS year,
                   count(DISTINCT activity_date) AS days, {sums}
            FROM read_parquet({hf_table('port_activity')})
            WHERE month(activity_date) BETWEEN {MONTHS[0]} AND {MONTHS[1]}
              AND year(activity_date) BETWEEN {years[0]} AND {years[-1]}
            GROUP BY ALL"""
    ).pl()
    profiles = conn.execute(
        f"""SELECT port_id, arg_max(port_name, ingested_at) AS port_name,
                   arg_max(iso3, ingested_at) AS iso3,
                   arg_max(continent, ingested_at) AS continent,
                   arg_max(latitude, ingested_at) AS latitude,
                   arg_max(longitude, ingested_at) AS longitude
            FROM read_parquet({hf_table('port_profiles')}) GROUP BY port_id"""
    ).pl()
    return windows.join(profiles, on="port_id", how="left")


def weekly_levels(windows: pl.DataFrame) -> pl.DataFrame:
    """Long format: port, year, metric, weekly level (sum ÷ weeks reported)."""
    days = windows.select(pl.col("days").max()).item()
    full = windows.filter(pl.col("days") >= 0.95 * days)  # drop part-reported windows
    return pl.concat([
        full.select(
            "port_id", "year", pl.lit(m).alias("metric"),
            (pl.sum_horizontal(cs) / (pl.col("days") / 7)).alias("weekly"),
        )
        for m, (cs, _) in METRICS.items()
    ])


def changes(levels: pl.DataFrame, pairs: Sequence[tuple[int, int]]) -> pl.DataFrame:
    """Log change per port, metric and year pair, minus its size band's median.

    Ports below MIN_WEEKLY (in the size metric, in the earlier year) are left out.
    """
    out = []
    for before, after in pairs:
        a = levels.filter(pl.col("year") == before).rename({"weekly": "w0"}).drop("year")
        b = levels.filter(pl.col("year") == after).rename({"weekly": "w1"}).drop("year")
        pair = a.join(b, on=["port_id", "metric"])
        size_metric = {m: s for m, (_, s) in METRICS.items()}
        size = (pair.select("port_id", pl.col("metric").alias("size_metric"),
                            pl.col("w0").alias("size")))
        pair = (
            pair.with_columns(pl.col("metric").replace_strict(size_metric).alias("size_metric"))
            .join(size, on=["port_id", "size_metric"])
            .with_columns(pl.col("size_metric").replace_strict(MIN_WEEKLY).alias("min"))
            .filter(pl.col("size") >= pl.col("min"))
            .with_columns(
                pl.when(pl.col("size") < BANDS[0] * pl.col("min")).then(pl.lit("small"))
                .when(pl.col("size") < BANDS[1] * pl.col("min")).then(pl.lit("medium"))
                .otherwise(pl.lit("large")).alias("band"),
                # +1 a week keeps a port that drops to zero finite.
                ((pl.col("w1") + 1) / (pl.col("w0") + 1)).log().alias("lr"),
                pl.lit(f"{before}-{after}").alias("pair"),
            )
        )
        out.append(pair.with_columns(
            (pl.col("lr") - pl.col("lr").median().over("metric", "band")).alias("effect")
        ).select("port_id", "metric", "pair", "band", "w0", "w1", "effect"))
    return pl.concat(out)


def label_ports(ports: pl.DataFrame) -> pl.DataFrame:
    """Add ``group`` (red_sea or the continent) and ``confounder``."""
    lat0, lat1, lon0, lon1 = RED_SEA_BOX
    by_name = {k: v for k, v in CONFOUNDED.items() if len(k) != 3 or not k.isupper()}
    by_iso = {k: v for k, v in CONFOUNDED.items() if k not in by_name}
    return ports.with_columns(
        pl.when(pl.col("latitude").is_between(lat0, lat1)
                & pl.col("longitude").is_between(lon0, lon1))
        .then(pl.lit("Red Sea")).otherwise(pl.col("continent")).alias("group"),
        pl.coalesce(pl.col("port_name").replace_strict(by_name, default=None),
                    pl.col("iso3").replace_strict(by_iso, default=None)).alias("confounder"),
    )


def flag(shock: pl.DataFrame, placebo: pl.DataFrame) -> pl.DataFrame:
    """Mark shock changes outside the band's placebo range and the port's own."""
    bands = placebo.group_by("metric", "band").agg(
        pl.col("effect").quantile(TAIL).alias("lo"),
        pl.col("effect").quantile(1 - TAIL).alias("hi"),
    )
    own = placebo.group_by("port_id", "metric").agg(
        pl.col("effect").abs().max().alias("own_max"), pl.len().alias("own_n"))
    return (
        shock.join(bands, on=["metric", "band"], how="left")
        .join(own, on=["port_id", "metric"], how="left")
        .with_columns(
            ((pl.col("effect") < pl.col("lo")) | (pl.col("effect") > pl.col("hi")))
            .alias("outside_band"),
        )
        .with_columns(
            (pl.col("outside_band") & (pl.col("effect").abs() > pl.col("own_max").fill_null(0)))
            .alias("flagged"),
            ((pl.col("effect").exp() - 1) * 100).alias("pct"),
        )
    )


def group_effects(shock: pl.DataFrame, placebo: pl.DataFrame) -> pl.DataFrame:
    """Mean change per group and metric, beside the same mean in each placebo pair."""
    def means(df: pl.DataFrame) -> pl.DataFrame:
        return df.group_by("group", "metric", "pair").agg(
            pl.col("effect").mean().alias("mean"), pl.len().alias("ports"))

    p = means(placebo).pivot(on="pair", index=["group", "metric"], values="mean")
    pairs = [f"{a}-{b}" for a, b in PLACEBOS if f"{a}-{b}" in p.columns]
    noise = (pl.concat_list([pl.col(c) ** 2 for c in pairs]).list.mean().sqrt() if pairs
             else pl.lit(None, dtype=pl.Float64))
    return (
        means(shock).drop("pair").join(p, on=["group", "metric"], how="left")
        .with_columns(noise.alias("noise"))
        .with_columns((pl.col("mean") / pl.col("noise")).alias("z"))
        .sort("metric", "group")
    )


def run(windows: pl.DataFrame) -> dict[str, pl.DataFrame]:
    ports = label_ports(windows.select(
        "port_id", "port_name", "iso3", "continent", "latitude", "longitude").unique("port_id"))
    levels = weekly_levels(windows)
    shock = changes(levels, [SHOCK]).join(ports, on="port_id", how="left")
    placebo = changes(levels, PLACEBOS).join(ports, on="port_id", how="left")
    flagged = flag(shock, placebo)
    counts = flagged.group_by("metric").agg(
        pl.len().alias("ports"),
        pl.col("outside_band").sum().alias("outside_band"),
        (pl.len() * 2 * TAIL).alias("expected_by_chance"),
        pl.col("flagged").sum().alias("flagged"),
        (pl.col("flagged") & (pl.col("effect") > 0)).sum().alias("gained"),
        (pl.col("flagged") & (pl.col("effect") < 0)).sum().alias("lost"),
    ).sort("metric")
    hubs = pl.col("port_name").is_in(ASIA_EUROPE_HUBS)
    groups = pl.concat([
        group_effects(shock, placebo),
        group_effects(shock.filter(hubs).with_columns(pl.lit("Asia–Europe hubs").alias("group")),
                      placebo.filter(hubs).with_columns(pl.lit("Asia–Europe hubs").alias("group"))),
    ], how="diagonal")
    return {"ports": flagged, "counts": counts, "groups": groups}


def _pct(df: pl.DataFrame, cols: Sequence[str]) -> pl.DataFrame:
    return df.with_columns([((pl.col(c).exp() - 1) * 100).alias(c) for c in cols])


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Red Sea port effects vs placebo years")
    parser.add_argument("--csv", type=Path, help="also write ports.csv and groups.csv here")
    args = parser.parse_args(argv)

    out = run(load_windows())
    print(markdown(out["counts"], list(out["counts"].columns)))
    shown = _pct(out["ports"].filter("flagged").sort("metric", "effect"), ["own_max"])
    print()
    print(markdown(shown, ["metric", "port_name", "iso3", "group", "w0", "w1", "pct",
                           "own_max", "confounder"]))
    groups = out["groups"]
    pct_cols = [c for c in groups.columns if c not in ("group", "metric", "ports", "z")]
    print()
    print(markdown(_pct(groups.filter(pl.col("ports") >= 3), pct_cols),
                   ["metric", "group", "ports", *pct_cols, "z"]))
    if args.csv:
        args.csv.mkdir(parents=True, exist_ok=True)
        out["ports"].write_csv(args.csv / "ports.csv")
        groups.write_csv(args.csv / "groups.csv")


if __name__ == "__main__":
    main()
