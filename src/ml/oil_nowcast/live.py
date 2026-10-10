"""Step 3 of ML4: the ``oil_trade_nowcast`` table, rebuilt by curation every run.

US seaborne oil exports (HS 2709–2711, Census vessel weight) for each month that
PortWatch has complete, about 4 weeks before Census publishes it. Only all-oil
exports passed step 1's target, so only that series is published.

Every month from 2020 gets the nowcast the model would have made with Census to the
month before (walk-forward, but from today's revised Census history), beside the
Census figure once it is out. The newest one or two months have no Census figure
yet: those are the live nowcasts. ``typical_error_pct`` and ``p90_error_pct`` are the
median and 90th percentile of the absolute % error over months from 2023.
"""
from __future__ import annotations

import logging
from datetime import UTC, date, datetime

import duckdb
import numpy as np
import polars as pl

from src.ml.oil_nowcast.us import (
    MODELS,
    TEST_FROM,
    build_panel,
    census_series,
    census_sql,
    portwatch_sql,
)

logger = logging.getLogger(__name__)

TABLE = "oil_trade_nowcast"
SERIES = "all_oil_exports"
#: Chosen in step 1 (lowest median error over 2021–2022 among the PortWatch models).
MODEL = "ratio12"
FIRST = date(2020, 1, 1)
INPUTS = ("us_trade_products", "port_activity", "port_profiles")

_SCHEMA = {
    "month": pl.Date, "series": pl.Utf8, "nowcast_tonnes": pl.Float64,
    "census_tonnes": pl.Float64, "error_pct": pl.Float64, "portwatch_tonnes": pl.Float64,
    "is_live": pl.Boolean, "model": pl.Utf8, "typical_error_pct": pl.Float64,
    "p90_error_pct": pl.Float64, "census_through": pl.Date, "built_at": pl.Datetime,
}


def nowcast_table(census: pl.DataFrame, portwatch: pl.DataFrame,
                  built_at: datetime | None = None) -> pl.DataFrame:
    """One row per month PortWatch has complete, from ``FIRST``; empty without inputs."""
    if census.filter(pl.col("series") == SERIES).is_empty() or portwatch.is_empty():
        return pl.DataFrame(schema=_SCHEMA)
    p = build_panel(census, portwatch, SERIES)
    model = MODELS[MODEL]
    rows = []
    for i, month in enumerate(p.months):
        if month < FIRST or not np.isfinite(p.coasts[i]).all():
            continue
        with np.errstate(all="ignore"):
            guess = model(p, i)
        if not np.isfinite(guess):
            continue
        actual = float(p.census[i]) if np.isfinite(p.census[i]) else None
        rows.append({
            "month": month, "nowcast_tonnes": guess, "census_tonnes": actual,
            "error_pct": None if actual is None else 100 * (guess / actual - 1),
            "portwatch_tonnes": float(p.portwatch[i]), "is_live": actual is None,
        })
    out = pl.DataFrame(rows, schema={k: _SCHEMA[k] for k in
                                     ("month", "nowcast_tonnes", "census_tonnes",
                                      "error_pct", "portwatch_tonnes", "is_live")})
    scored = out.filter(pl.col("error_pct").is_not_null()
                        & (pl.col("month").dt.year() >= TEST_FROM))["error_pct"].abs()
    published = census.filter((pl.col("series") == SERIES) & pl.col("tonnes").is_not_null())
    return out.with_columns(
        pl.lit(SERIES).alias("series"),
        pl.lit(MODEL).alias("model"),
        pl.lit(scored.median() if scored.len() else None, dtype=pl.Float64)
        .alias("typical_error_pct"),
        pl.lit(scored.quantile(0.9) if scored.len() else None, dtype=pl.Float64)
        .alias("p90_error_pct"),
        pl.lit(published["month"].max(), dtype=pl.Date).alias("census_through"),
        pl.lit(built_at or datetime.now(UTC).replace(tzinfo=None), dtype=pl.Datetime)
        .alias("built_at"),
    ).select(list(_SCHEMA)).sort("month")


def create_oil_trade_nowcast(conn: duckdb.DuckDBPyConnection) -> int:
    """Rebuild ``oil_trade_nowcast`` from the pipeline's tables. Returns its row count."""
    have = {r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables")
            .fetchall()}
    missing = [t for t in INPUTS if t not in have]
    if missing:
        logger.info("Skipping %s: no %s table", TABLE, ", ".join(missing))
        return 0
    census = census_series(conn.execute(census_sql("us_trade_products")).pl())
    portwatch = conn.execute(portwatch_sql("port_activity", "port_profiles")).pl()
    table = nowcast_table(census, portwatch)
    conn.register("_oil_nowcast", table.to_arrow())
    try:
        conn.execute(f"CREATE OR REPLACE TABLE {TABLE} AS SELECT * FROM _oil_nowcast")
    finally:
        conn.unregister("_oil_nowcast")
    live = table.filter("is_live")
    logger.info("Created %s with %d rows; live months: %s", TABLE, table.height,
                ", ".join(str(m) for m in live["month"]) or "none")
    return table.height
