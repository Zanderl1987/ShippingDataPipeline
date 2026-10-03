"""Backtest the season export forecast against simpler estimates.

    python -m src.ml.grain_forecast.backtest              # local pipeline.db
    python -m src.ml.grain_forecast.backtest --hf         # read HF, save nothing
    python -m src.ml.grain_forecast.backtest --db other.duckdb

Each season is forecast using only seasons that had finished before it began.
Errors are the median absolute percentage miss against the season's final
shipped total (USDA export sales), by crop, anchor and stage of the season.
"USDA" is USDA's latest projection with the same level correction the
forecast fits, so the 2-4% gap between how USDA and the export sales program
count exports does not count against it.
"""
from __future__ import annotations

import argparse
from datetime import date, timedelta

import duckdb
import polars as pl

from src.analytics.grain_demand import COMMODITIES, create_grain_export_pace
from src.ml.grain_forecast.model import forecast_seasons, season_frame
from src.storage.writer import get_db_path

HF_REPO = "ZanderL1337/shipping-data-pipeline"
#: Stages of the season in marketing-year weeks; for corn and soybeans they are
#: Sep-Oct, Nov-Dec, Jan-Mar and Apr-Aug, for wheat Jun-Jul, Aug-Sep, Oct-Dec
#: and Jan-May.
STAGES = [(1, 8), (9, 17), (18, 30), (31, 53)]
MODELS = {"last_season_mt": "last season", "pace_projection_mt": "pace",
          "usda_alone_mt": "USDA", "forecast_mt": "forecast"}
#: USDA's projection alone (pace weight 0), level-corrected, for every crop.
USDA_ALONE = {("Corn", "usda"): 0.0, ("Soybeans", "usda"): 0.0, ("Wheat", "usda"): 0.0,
              ("Corn", "last_season"): 1.0}


def hf_connection() -> duckdb.DuckDBPyConnection:
    """An in-memory DuckDB with the inputs read from the HF dataset."""
    conn = duckdb.connect()
    conn.execute("INSTALL httpfs; LOAD httpfs;")
    for table in ("us_export_sales", "usda_wasde"):
        source = f"hf://datasets/{HF_REPO}/{table}/{table}.parquet"
        conn.execute(f"CREATE TABLE {table} AS SELECT * FROM read_parquet('{source}')")
    create_grain_export_pace(conn)
    return conn


def run(frame: pl.DataFrame) -> pl.DataFrame:
    """Every forecast, with USDA's projection alone alongside."""
    keys = ["commodity", "marketing_year", "my_week"]
    forecasts = forecast_seasons(frame)
    usda = forecast_seasons(frame, fixed_weights=USDA_ALONE).filter(
        pl.col("anchor") == "usda"
    ).select(*keys, pl.col("forecast_mt").alias("usda_alone_mt"))
    return forecasts.join(usda, on=keys, how="left")


def score(forecasts: pl.DataFrame) -> pl.DataFrame:
    done = forecasts.filter(pl.col("final_mt").is_not_null())
    stage = pl.lit(None, dtype=pl.Utf8)
    for lo, hi in reversed(STAGES):
        stage = (pl.when(pl.col("my_week").is_between(lo, hi))
                 .then(pl.lit(f"weeks {lo:>2}-{hi}")).otherwise(stage))
    done = done.with_columns(stage.alias("stage"))
    return done.group_by("commodity", "anchor", "stage").agg(
        pl.col("marketing_year").n_unique().alias("seasons"),
        *[
            ((pl.col(col) / pl.col("final_mt") - 1).abs().median() * 100).round(1).alias(name)
            for col, name in MODELS.items()
        ],
        (pl.col("final_mt").is_between(pl.col("forecast_low_mt"), pl.col("forecast_high_mt"))
         .mean() * 100).round(0).alias("in range %"),
    ).with_columns(
        pl.struct("commodity", "stage").map_elements(
            lambda r: _months(r["commodity"], r["stage"]), return_dtype=pl.Utf8,
        ).alias("months"),
    ).sort("commodity", "anchor", "stage", descending=[False, True, False])


def _months(commodity: str, stage: str) -> str:
    """'Sep-Oct' for weeks 1-8 of a season starting in September."""
    lo, hi = (int(w) for w in stage.removeprefix("weeks ").split("-"))
    start = date(2001, COMMODITIES.get(commodity, 9), 1)
    first = start + timedelta(days=(lo - 1) * 7 + 6)  # the month a stage's first week ends in
    last = start + timedelta(days=min(hi * 7, 365) - 1)
    return f"{first:%b}-{last:%b}"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--hf", action="store_true", help="read the inputs from HF")
    parser.add_argument("--db", help="DuckDB file holding grain_export_pace and usda_wasde")
    parser.add_argument("--out", help="also write every forecast to this parquet file")
    args = parser.parse_args(argv)

    if args.hf:
        conn = hf_connection()
    else:
        conn = duckdb.connect(args.db or str(get_db_path()), read_only=True)
    forecasts = run(season_frame(conn))
    tested = forecasts.filter(pl.col("final_mt").is_not_null())["marketing_year"]
    print(f"Seasons forecast: {tested.n_unique()} ({tested.min()!s} to {tested.max()!s})")
    with pl.Config(tbl_rows=50, tbl_cols=20, tbl_width_chars=200, float_precision=1,
                   tbl_hide_dataframe_shape=True, tbl_formatting="ASCII_MARKDOWN"):
        print("Median miss vs the final total, %. Anchor: what the forecast starts from.")
        print(score(forecasts))
    if args.out:
        forecasts.write_parquet(args.out)


if __name__ == "__main__":
    main()
