from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import polars as pl
import pytest

from src.ml.oil_nowcast.live import TABLE, create_oil_trade_nowcast, nowcast_table
from tests.ml.test_oil_nowcast import _inputs


def test_nowcast_table_marks_months_without_census_as_live() -> None:
    census, pw = _inputs(60)
    last = census["month"].max()
    census = census.filter(pl.col("month") != last)  # Census hasn't published it yet
    out = nowcast_table(census, pw, built_at=datetime(2026, 10, 10))
    assert out["month"].min() == date(2020, 1, 1)
    live = out.filter("is_live")
    assert live["month"].to_list() == [last] and live["census_tonnes"][0] is None
    assert live["nowcast_tonnes"][0] > 0
    # Census = half of PortWatch exactly, so the ratio model is right to the tonne.
    assert out["typical_error_pct"][0] == pytest.approx(0, abs=1e-9)
    assert out["census_through"][0] == census["month"].max()


def test_empty_inputs_give_an_empty_table() -> None:
    census, pw = _inputs(3)
    assert nowcast_table(census.head(0), pw).is_empty()
    assert nowcast_table(census, pw.head(0)).columns == nowcast_table(census, pw).columns


def _raw_tables(conn: duckdb.DuckDBPyConnection, months: int = 30) -> None:
    days = pl.date_range(date(2019, 1, 1), date(2019, 1, 1) + timedelta(days=31 * months),
                         "1d", eager=True)
    activity = pl.DataFrame({
        "port_id": "p1", "iso3": "USA", "activity_date": days,
        "export_tanker": [1000.0 + (d.month * 10) for d in days], "import_tanker": 500.0,
    })
    month_starts = sorted({d.replace(day=1) for d in days})[:-2]  # Census lags
    tonnes = activity.group_by(pl.col("activity_date").dt.truncate("1mo").alias("m")).agg(
        pl.col("export_tanker").sum())
    trade = (tonnes.filter(pl.col("m").is_in(month_starts))
             .select(pl.col("m").alias("period_date"), pl.lit("X").alias("flow_code"),
                     pl.lit("2709001000").alias("commodity_code"),
                     pl.lit("domestic").alias("export_origin"),
                     (pl.col("export_tanker") * 1000).alias("vessel_weight_kg"),
                     pl.lit(datetime(2026, 1, 1)).alias("ingested_at")))
    profiles = pl.DataFrame({"port_id": ["p1"], "latitude": [29.7], "longitude": [-95.0],
                             "ingested_at": [datetime(2026, 1, 1)]})
    for name, df in (("port_activity", activity), ("us_trade_products", trade),
                     ("port_profiles", profiles)):
        conn.register(f"_{name}", df.to_arrow())
        conn.execute(f"CREATE TABLE {name} AS SELECT * FROM _{name}")


def test_create_builds_the_table_from_the_pipeline_tables() -> None:
    conn = duckdb.connect()
    assert create_oil_trade_nowcast(conn) == 0  # inputs missing: skipped, no table
    _raw_tables(conn)
    n = create_oil_trade_nowcast(conn)
    out = conn.execute(f"SELECT * FROM {TABLE} ORDER BY month").pl()
    assert n == out.height > 0
    assert out.filter("is_live").height >= 1  # PortWatch months past Census's newest
    done = out.filter(~pl.col("is_live"))
    assert (done["error_pct"].abs() < 1e-6).all()  # Census = PortWatch (Gulf port)
