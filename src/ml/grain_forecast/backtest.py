"""Backtest the season export forecast against the two simple estimates.

    python -m src.ml.grain_forecast.backtest              # local pipeline.db
    python -m src.ml.grain_forecast.backtest --hf         # read HF, save nothing
    python -m src.ml.grain_forecast.backtest --db other.duckdb

Each season is forecast using only seasons that had finished before it began.
Errors are the median absolute percentage miss against the season's final
shipped total, by crop and stage of the season.
"""
from __future__ import annotations

import argparse

import duckdb
import polars as pl

from src.analytics.grain_demand import create_grain_export_pace
from src.ml.grain_forecast.model import forecast_seasons, season_frame
from src.storage.writer import get_db_path

HF_REPO = "ZanderL1337/shipping-data-pipeline"
STAGES = [(1, 8, "weeks 1-8 (Sep-Oct)"), (9, 17, "weeks 9-17 (Nov-Dec)"),
          (18, 30, "weeks 18-30 (Jan-Mar)"), (31, 53, "weeks 31-53 (Apr-Aug)")]
MODELS = {"last_season_mt": "last season", "pace_projection_mt": "pace",
          "forecast_mt": "forecast"}


def hf_connection() -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with ``us_export_sales`` read from the HF dataset."""
    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    source = f"hf://datasets/{HF_REPO}/us_export_sales/us_export_sales.parquet"
    conn.execute(f"CREATE TABLE us_export_sales AS SELECT * FROM read_parquet('{source}')")
    create_grain_export_pace(conn)
    return conn


def score(forecasts: pl.DataFrame) -> pl.DataFrame:
    done = forecasts.filter(pl.col("final_mt").is_not_null())
    stage = pl.lit(None, dtype=pl.Utf8)
    for lo, hi, name in reversed(STAGES):
        stage = pl.when(pl.col("my_week").is_between(lo, hi)).then(pl.lit(name)).otherwise(stage)
    done = done.with_columns(stage.alias("stage"))
    return done.group_by("commodity", "stage").agg(
        pl.col("marketing_year").n_unique().alias("seasons"),
        *[
            ((pl.col(col) / pl.col("final_mt") - 1).abs().median() * 100).round(1).alias(name)
            for col, name in MODELS.items()
        ],
        (pl.col("final_mt").is_between(pl.col("forecast_low_mt"), pl.col("forecast_high_mt"))
         .mean() * 100).round(0).alias("in 80% range"),
    ).sort("commodity", "stage")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--hf", action="store_true", help="read us_export_sales from HF")
    parser.add_argument("--db", help="DuckDB file holding grain_export_pace")
    parser.add_argument("--out", help="also write every forecast to this parquet file")
    args = parser.parse_args(argv)

    if args.hf:
        conn = hf_connection()
    else:
        conn = duckdb.connect(args.db or str(get_db_path()), read_only=True)
    forecasts = forecast_seasons(season_frame(conn))
    tested = forecasts.filter(pl.col("final_mt").is_not_null())["marketing_year"]
    print(f"Seasons forecast: {tested.n_unique()} ({tested.min()!s} to {tested.max()!s})")
    with pl.Config(tbl_rows=50, tbl_cols=20, tbl_width_chars=200, float_precision=1,
                   tbl_hide_dataframe_shape=True, tbl_formatting="ASCII_MARKDOWN"):
        print("Median miss vs the final total, %:")
        print(score(forecasts))
    if args.out:
        forecasts.write_parquet(args.out)


if __name__ == "__main__":
    main()
