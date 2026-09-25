"""Score the disruption-warning rules (``baselines.py``) week by week.

    python -m src.ml.disruption_warning.backtest            # local pipeline.db
    python -m src.ml.disruption_warning.backtest --hf       # read HF remotely, save nothing

The event radius is tuned on origins before ``--start`` (2020-2022) and then
scored, untouched, on origins from ``--start`` (2023 on).
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import duckdb
import polars as pl

from src.ml.disruption_warning.baselines import (
    FLAG_RULES,
    RULES,
    event_exposure,
    prepare_events,
    rule_scores,
    warning_rows,
    weekly_origins,
)
from src.ml.disruption_warning.evaluate import BUDGET, average_precision, flag_stats, score
from src.ml.disruption_warning.labels import label_drops
from src.ml.port_forecast.backtest import hf_connection, hf_table, load_weekly_hf, load_weekly_local
from src.ml.port_forecast.baselines import weekly_grid
from src.storage.writer import get_db_path

RADII_KM = (0.0, 100.0, 200.0, 300.0, 500.0, 800.0)  # 0: only events that list the port
TUNE_START = date(2020, 1, 6)
EVENT_COLUMNS = "event_id, event_type, alert_level, from_date, latitude, longitude, affected_ports"
PROFILE_COLUMNS = "port_id, latitude, longitude"


def load_inputs(hf: bool) -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Weekly calls, raw disruption events and port locations."""
    if hf:
        conn = hf_connection()

        def read(table: str, cols: str) -> pl.DataFrame:
            return conn.execute(f"SELECT {cols} FROM read_parquet({hf_table(table)})").pl()

        return (
            load_weekly_hf(),
            read("disruption_events", EVENT_COLUMNS),
            read("port_profiles", PROFILE_COLUMNS),
        )
    with duckdb.connect(str(get_db_path()), read_only=True) as conn:
        events = conn.execute(f"SELECT {EVENT_COLUMNS} FROM disruption_events").pl()
        profiles = conn.execute(f"SELECT {PROFILE_COLUMNS} FROM port_profiles").pl()
    return load_weekly_local(), events, profiles


def tune_radius(rows: pl.DataFrame) -> tuple[float, pl.DataFrame]:
    """The radius with the best average precision for the ``gdacs`` rule."""
    table = pl.DataFrame(
        [
            {"radius_km": r, "ap_gdacs": average_precision(rule_scores(rows, r), "gdacs")}
            for r in RADII_KM
        ]
    )
    best = table.sort("ap_gdacs", descending=True, nulls_last=True)["radius_km"][0]
    return float(best), table


def run(
    weekly: pl.DataFrame, raw_events: pl.DataFrame, profiles: pl.DataFrame, start: date
) -> tuple[float, pl.DataFrame, dict[str, pl.DataFrame]]:
    labels = label_drops(weekly_grid(weekly))
    exposure = event_exposure(prepare_events(raw_events), profiles)
    tune = warning_rows(
        labels, exposure, weekly_origins(labels, TUNE_START, start - timedelta(weeks=1))
    )
    radius, tuning = tune_radius(tune)
    rows = rule_scores(warning_rows(labels, exposure, weekly_origins(labels, start)), radius)
    rows = rows.with_columns(pl.col("origin_week").dt.year().alias("origin_year"))
    tables = {
        f"radius tuning (2020-2022 origins, {tune.height:,} rows)": tuning,
        "all": score(rows, RULES),
        "by horizon": score(rows, RULES, by=["horizon"]),
        "by port size": score(rows, RULES, by=["size_band"]),
        "by year": score(rows, RULES, by=["origin_year"]),
        "flags on their own": flag_stats(rows, FLAG_RULES),
    }
    return radius, rows, tables


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--hf", action="store_true", help="read the tables from HF")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 2))
    parser.add_argument("--out", help="also write the scored rows to this parquet file")
    args = parser.parse_args(argv)

    radius, rows, tables = run(*load_inputs(args.hf), args.start)
    origins = rows["origin_week"]
    print(
        f"{origins.n_unique()} weekly origins from {origins.min()!s} to {origins.max()!s}, "
        f"{rows.height:,} port-weeks, {int(rows['y'].sum()):,} disruptions "
        f"({int(rows['y'].sum()) / rows.height:.2%}); event radius {radius:.0f} km; "
        f"alert budget {BUDGET:.0%} of ports a week"
    )
    with pl.Config(
        tbl_rows=100, tbl_cols=20, float_precision=3, tbl_hide_dataframe_shape=True,
        tbl_formatting="ASCII_MARKDOWN", tbl_hide_column_data_types=True,
    ):
        for name, table in tables.items():
            print(f"\n{name}\n{table}")
    if args.out:
        rows.write_parquet(args.out)


if __name__ == "__main__":
    main()
