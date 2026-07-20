from __future__ import annotations

from datetime import date, datetime
from typing import Any

import duckdb
import polars as pl

from src.config import settings
from src.storage.writer import get_db_path


def query(
    sql: str,
    params: list[Any] | None = None,
) -> pl.DataFrame:
    db_path = get_db_path()
    conn = duckdb.connect(str(db_path))
    conn.execute("SET autoinstall_known_extensions=1;")
    conn.execute("SET autoload_known_extensions=1;")

    if params:
        result = conn.execute(sql, params)
    else:
        result = conn.execute(sql)

    arrow_table = result.to_arrow_table()
    conn.close()
    result_df = pl.from_arrow(arrow_table)
    assert isinstance(result_df, pl.DataFrame)
    return result_df


def read_dataset(
    table_name: str,
    date_from: date | None = None,
    date_to: date | None = None,
    source: str | None = None,
    limit: int | None = None,
) -> pl.DataFrame:
    clauses: list[str] = []
    params: list[Any] = []

    if date_from:
        clauses.append("partition_date >= ?::date")
        params.append(date_from.isoformat())
    if date_to:
        clauses.append("partition_date <= ?::date")
        params.append(date_to.isoformat())
    if source:
        clauses.append("source = ?")
        params.append(source)

    where = ""
    if clauses:
        where = "WHERE " + " AND ".join(clauses)

    limit_clause = ""
    if limit:
        limit_clause = f"LIMIT {limit}"

    sql = f"SELECT * FROM {table_name} {where} {limit_clause}"
    return query(sql, params if params else None)


def list_sources() -> pl.DataFrame:
    parquet_root = settings.storage_dir / "parquet" / "raw"
    if not parquet_root.exists():
        return pl.DataFrame(
            {"source": [], "latest_partition": [], "row_count": []}
        )

    sources: list[dict[str, Any]] = []
    for p in parquet_root.iterdir():
        if p.is_dir():
            parquet_files = list(p.rglob("*.parquet"))
            if not parquet_files:
                continue
            latest = max(f.stat().st_mtime for f in parquet_files)
            total_rows = sum(
                pl.read_parquet(str(f)).height for f in parquet_files
            )
            sources.append(
                {
                    "source": p.name,
                    "latest_partition": datetime.fromtimestamp(latest),
                    "row_count": total_rows,
                }
            )

    return pl.DataFrame(sources)


def get_latest_timestamp(
    table_name: str, source: str
) -> datetime | None:
    sql = f"""
        SELECT max(timestamp) as max_ts
        FROM {table_name}
        WHERE source = ?
    """
    result = query(sql, [source])
    if result.height == 0:
        return None
    val = result[0, "max_ts"]
    if val is None:
        return None
    return datetime.fromisoformat(str(val))
