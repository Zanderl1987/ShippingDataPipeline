from __future__ import annotations

from pathlib import Path

import duckdb
import polars as pl

from src.config import settings
from src.storage.schema import ALL_TABLES, TableSchema


def get_db_path() -> Path:
    return settings.storage_dir / "pipeline.db"


def init_db() -> duckdb.DuckDBPyConnection:
    db_path = get_db_path()
    conn = duckdb.connect(str(db_path))
    for table in ALL_TABLES:
        conn.execute(table.create_sql())
    return conn


def write_raw(
    source: str,
    df: pl.DataFrame,
    table_name: str = "ais_positions",
) -> int:
    db_path = get_db_path()
    conn = duckdb.connect(str(db_path))
    conn.execute("SET autoinstall_known_extensions=1;")
    conn.execute("SET autoload_known_extensions=1;")

    schema = _find_table(table_name)
    partition_cols = schema.partition_cols if schema else []

    if "partition_date" in df.columns:
        df = df.with_columns(
            pl.col("partition_date").cast(pl.Date)
        )

    insert_cols = [c for c in df.columns if c != "ingested_at"]
    col_list = ", ".join(insert_cols)

    if partition_cols:
        base = settings.storage_dir / "parquet" / "raw" / source
        base.mkdir(parents=True, exist_ok=True)
        df.write_parquet(str(base), partition_by=partition_cols)
        conn.execute(
            f"INSERT INTO {table_name}({col_list}) SELECT {col_list} FROM df;"
        )
    else:
        conn.execute(
            f"INSERT OR REPLACE INTO {table_name}({col_list}) SELECT {col_list} FROM df;"
        )

    row = conn.execute(
        f"SELECT count(*) FROM {table_name} WHERE source = ?",
        [source],
    ).fetchone()
    assert row is not None
    count: int = row[0]
    conn.close()
    return count


def write_curated(
    dataset: str,
    df: pl.DataFrame,
    table_name: str | None = None,
) -> int:
    dest = table_name or dataset
    db_path = get_db_path()
    conn = duckdb.connect(str(db_path))
    conn.execute("SET autoinstall_known_extensions=1;")
    conn.execute("SET autoload_known_extensions=1;")

    conn.execute(
        f"CREATE TABLE IF NOT EXISTS curated_{dest} AS SELECT * FROM df WHERE 1=0;"
    )
    conn.execute(f"INSERT OR REPLACE INTO curated_{dest} SELECT * FROM df;")
    row = conn.execute(f"SELECT count(*) FROM curated_{dest}").fetchone()
    assert row is not None
    count: int = row[0]
    conn.close()
    return count


def _find_table(name: str) -> TableSchema | None:
    for t in ALL_TABLES:
        if t.name == name:
            return t
    return None
