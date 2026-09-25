"""Backtest the weekly port-call forecasts.

    python -m src.ml.port_forecast.backtest            # local pipeline.db
    python -m src.ml.port_forecast.backtest --hf       # read HF remotely, save nothing

With ``--hf`` the daily ``port_activity`` parquet is read straight from the HF
dataset and summed to weeks in memory, so the ~160 MB file never lands on disk.
"""
from __future__ import annotations

import argparse
import os
from datetime import date

import duckdb
import polars as pl

from src.analytics.port_weekly import create_port_weekly
from src.ml.port_forecast.baselines import (
    BASELINES,
    backtest_origins,
    baseline_forecasts,
    weekly_grid,
)
from src.ml.port_forecast.evaluate import REFERENCE, score
from src.storage.writer import get_db_path

HF_REPO = "ZanderL1337/shipping-data-pipeline"
WEEKLY_COLUMNS = "port_id, week_start, is_complete_week, portcalls"


def load_weekly_local() -> pl.DataFrame:
    with duckdb.connect(str(get_db_path()), read_only=True) as conn:
        return conn.execute(f"SELECT {WEEKLY_COLUMNS} FROM port_weekly").pl()


def hf_connection() -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB that can read the HF dataset (``hf://`` paths)."""
    from huggingface_hub import get_token

    conn = duckdb.connect()  # in memory
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    token = os.environ.get("HF_TOKEN") or get_token()
    if token:
        conn.execute(f"CREATE SECRET hf (TYPE huggingface, TOKEN '{token}')")
    return conn


def hf_table(table: str, revision: str = "main") -> str:
    return f"'hf://datasets/{HF_REPO}@{revision}/{table}/{table}.parquet'"


def load_weekly_hf(revision: str = "main") -> pl.DataFrame:
    conn = hf_connection()
    source = hf_table("port_activity", revision)
    conn.execute(f"CREATE VIEW port_activity AS SELECT * FROM read_parquet({source})")
    create_port_weekly(conn)
    return conn.execute(f"SELECT {WEEKLY_COLUMNS} FROM port_weekly").pl()


def run(weekly: pl.DataFrame, start: date) -> tuple[pl.DataFrame, dict[str, pl.DataFrame]]:
    """Forecasts for every origin, and scores by horizon, size, and year."""
    grid = weekly_grid(weekly)
    forecasts = baseline_forecasts(grid, backtest_origins(grid, start)).with_columns(
        pl.col("origin_week").dt.year().alias("origin_year")
    )
    scores = {
        "by horizon": score(forecasts, BASELINES),
        "by horizon and port size": score(forecasts, BASELINES, by=["horizon", "size_band"]),
        "by year (all horizons)": score(forecasts, BASELINES, by=["origin_year"]),
    }
    return forecasts, scores


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--hf", action="store_true", help="read port_activity from HF")
    parser.add_argument("--start", type=date.fromisoformat, default=date(2023, 1, 2))
    parser.add_argument("--out", help="also write the forecasts to this parquet file")
    args = parser.parse_args(argv)

    weekly = load_weekly_hf() if args.hf else load_weekly_local()
    forecasts, scores = run(weekly, args.start)
    origins = forecasts["origin_week"]
    print(
        f"{origins.n_unique()} origins from {origins.min()!s} to {origins.max()!s}, "
        f"{forecasts.height:,} forecasts per model"
    )
    with pl.Config(
        float_precision=3,
        tbl_rows=50,
        tbl_cols=20,
        tbl_width_chars=200,
        tbl_hide_dataframe_shape=True,
        tbl_formatting="ASCII_MARKDOWN",  # box-drawing chars break cp1252 consoles
    ):
        for title, table in scores.items():
            keys = [c for c in table.columns if not c.startswith(("wape_", "vs_ref_"))]
            for prefix, what in (("wape_", "WAPE"), ("vs_ref_", f"error vs {REFERENCE}")):
                picked = table.select(
                    *keys,
                    *[
                        pl.col(c).alias(c.removeprefix(prefix))
                        for c in table.columns
                        if c.startswith(prefix)
                    ],
                )
                print(f"\n{what}, {title}\n{picked}")
    if args.out:
        forecasts.write_parquet(args.out)


if __name__ == "__main__":
    main()
