"""Write raw and curated data to DuckDB and Parquet."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import duckdb
import polars as pl

from src.config import settings
from src.storage.schema import ALL_TABLES, TableSchema

logger = logging.getLogger(__name__)

_VALID_TABLE_NAMES = {t.name for t in ALL_TABLES}


def get_db_path() -> Path:
    return settings.storage_dir / "pipeline.db"


def get_connection(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    """Open a DuckDB connection with consistent WAL settings.

    DuckDB uses WAL by default for read-write connections, allowing
    concurrent reads while a write transaction is active.
    """
    db_path = get_db_path()
    conn = duckdb.connect(str(db_path), read_only=read_only)
    if not read_only:
        conn.execute("PRAGMA enable_progress_bar")
    return conn


def init_db() -> duckdb.DuckDBPyConnection:
    conn = get_connection()
    for table in ALL_TABLES:
        conn.execute(table.create_sql())
    try:
        from src.storage.migrations import apply_pending_migrations
        apply_pending_migrations(conn)
    except Exception as e:
        # Deliberately non-fatal: a migration problem should not take down all
        # collection. But it means the schema may be stale, which silently
        # drops columns on write, so it is an error rather than a warning.
        logging.getLogger(__name__).error(
            "Migration runner failed: %s. Schema may be out of date.", e
        )
    return conn


def _validate_dataset_name(name: str) -> str:
    """Validate and sanitize a dataset/table name for safe SQL interpolation."""
    if not re.match(r"^[a-zA-Z_][a-zA-Z0-9_]*$", name):
        raise ValueError(
            f"Invalid dataset name: {name!r}. "
            "Must contain only letters, digits, and underscores, "
            "and start with a letter or underscore."
        )
    return name


def _table_has_pk(conn: duckdb.DuckDBPyConnection, table_name: str) -> bool:
    """Check if a table has a PRIMARY KEY constraint."""
    result = conn.execute(
        "SELECT constraint_type FROM information_schema.table_constraints "
        "WHERE table_name = ? AND constraint_type = 'PRIMARY KEY'",
        [table_name],
    ).fetchall()
    return len(result) > 0


def _upsert_on_keys(
    conn: duckdb.DuckDBPyConnection,
    table_name: str,
    col_list: str,
    keys: list[str],
    df: pl.DataFrame,
) -> None:
    """Replace rows matching the incoming batch's natural key, then insert.

    Partitioned tables have no PRIMARY KEY, so `INSERT OR REPLACE` and
    `ON CONFLICT` are unavailable. `IS NOT DISTINCT FROM` is used rather than
    `=` so a NULL key component still matches itself.
    """
    pred = " AND ".join(f"t.{k} IS NOT DISTINCT FROM d.{k}" for k in keys)
    conn.execute("BEGIN TRANSACTION;")
    try:
        conn.execute(
            f"DELETE FROM {table_name} AS t "
            f"WHERE EXISTS (SELECT 1 FROM df AS d WHERE {pred});"
        )
        conn.execute(
            f"INSERT INTO {table_name}({col_list}) SELECT {col_list} FROM df;"
        )
        conn.execute("COMMIT;")
    except Exception:
        conn.execute("ROLLBACK;")
        raise


def _export_partitions(
    conn: duckdb.DuckDBPyConnection,
    base: Path,
    table_name: str,
    col_list: str,
    partition_cols: list[str],
    df: pl.DataFrame,
) -> None:
    """Rewrite the parquet partitions this batch touched, from the table.

    Polars overwrites a partition directory wholesale, so writing the batch
    alone would discard rows an earlier run put in the same partition (JODI
    primary and secondary products share one `period`). Exporting the merged
    rows straight out of DuckDB keeps the two stores identical by construction.
    """
    pred = " AND ".join(f"t.{c} IS NOT DISTINCT FROM d.{c}" for c in partition_cols)
    merged = conn.execute(
        f"SELECT {col_list} FROM {table_name} AS t "
        f"WHERE EXISTS (SELECT 1 FROM df AS d WHERE {pred});"
    ).pl()
    base.mkdir(parents=True, exist_ok=True)
    merged.write_parquet(str(base), partition_by=partition_cols)


def write_raw(
    source: str,
    df: pl.DataFrame,
    table_name: str = "ais_positions",
) -> int:
    _validate_dataset_name(table_name)
    conn = get_connection()
    try:
        conn.execute("SET autoinstall_known_extensions=1;")
        conn.execute("SET autoload_known_extensions=1;")

        schema = _find_table(table_name)
        partition_cols = schema.partition_cols if schema else []

        if "partition_date" in df.columns:
            df = df.with_columns(
                pl.col("partition_date").cast(pl.Date)
            )

        table_columns = _get_table_columns(conn, table_name)
        if not table_columns and table_name in _VALID_TABLE_NAMES:
            # Known table that was never created — create the full schema now
            # rather than silently discarding the rows.
            for t in ALL_TABLES:
                conn.execute(t.create_sql())
            table_columns = _get_table_columns(conn, table_name)

        insert_cols = [c for c in df.columns if c != "ingested_at" and c in table_columns]
        col_list = ", ".join(insert_cols)

        if not insert_cols:
            raise ValueError(
                f"No columns from the DataFrame match table {table_name!r} "
                f"(table columns: {sorted(table_columns)}; "
                f"df columns: {df.columns}). Refusing to silently write 0 rows."
            )

        dropped = set(df.columns) - set(insert_cols) - {"ingested_at"}
        if dropped:
            logger.warning("Dropping columns not in %s: %s", table_name, dropped)

        df = df.select(insert_cols)

        dedup_keys = schema.dedup_keys if schema else []
        missing_keys = [k for k in dedup_keys if k not in table_columns]
        if missing_keys:
            # The key can't be evaluated against a column the table lacks.
            logger.warning(
                "Appending to %s without dedup: key columns missing from the "
                "table: %s",
                table_name,
                missing_keys,
            )
            dedup_keys = []

        absent = [k for k in dedup_keys if k not in df.columns]
        if absent:
            # A key column the source omits entirely lands as NULL, and the
            # upsert matches with IS NOT DISTINCT FROM — so supply the NULL
            # rather than giving up on dedup (axiomancer sends no mmsi).
            df = df.with_columns([pl.lit(None).alias(k) for k in absent])
            logger.info(
                "Dedup key columns absent from the %s batch, matched as NULL: %s",
                table_name,
                absent,
            )

        if dedup_keys:
            before = df.height
            df = df.unique(subset=dedup_keys, keep="last")
            if df.height < before:
                # A large collapse usually means the key doesn't fit the feed
                # (a source that leaves a key column null flattens its whole
                # batch), so it is worth seeing in the logs either way.
                logger.info(
                    "Collapsed %d of %d %s rows on %s",
                    before - df.height,
                    before,
                    table_name,
                    dedup_keys,
                )
        rows_written = df.height

        if dedup_keys:
            _upsert_on_keys(conn, table_name, col_list, dedup_keys, df)
        elif _table_has_pk(conn, table_name):
            # INSERT OR REPLACE updates only the listed columns, so without
            # this a rewritten row kept its first ingested_at forever.
            stamp_cols, stamp_vals = col_list, col_list
            if "ingested_at" in table_columns:
                stamp_cols += ", ingested_at"
                stamp_vals += ", now()"
            conn.execute(
                f"INSERT OR REPLACE INTO {table_name}({stamp_cols}) "
                f"SELECT {stamp_vals} FROM df;"
            )
        else:
            conn.execute(
                f"INSERT INTO {table_name}({col_list}) SELECT {col_list} FROM df;"
            )

        if partition_cols and all(c in insert_cols for c in partition_cols):
            _export_partitions(
                conn,
                settings.storage_dir / "parquet" / "raw" / source,
                table_name,
                col_list,
                partition_cols,
                df,
            )
        elif partition_cols:
            logger.warning(
                "Skipping parquet export for %s: partition columns %s not in "
                "the batch",
                table_name,
                [c for c in partition_cols if c not in insert_cols],
            )

        return rows_written
    finally:
        conn.close()


def write_curated(
    dataset: str,
    df: pl.DataFrame,
    table_name: str | None = None,
) -> int:
    dest = _validate_dataset_name(table_name or dataset)
    conn = get_connection()
    try:
        conn.execute("SET autoinstall_known_extensions=1;")
        conn.execute("SET autoload_known_extensions=1;")

        conn.execute(
            f"CREATE OR REPLACE TABLE curated_{dest} AS SELECT * FROM df;"
        )
        row = conn.execute(f"SELECT count(*) FROM curated_{dest}").fetchone()
        assert row is not None
        count: int = row[0]
        return count
    finally:
        conn.close()


def _find_table(name: str) -> TableSchema | None:
    for t in ALL_TABLES:
        if t.name == name:
            return t
    return None


def _get_table_columns(conn: duckdb.DuckDBPyConnection, table_name: str) -> set[str]:
    result = conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
        [table_name],
    ).fetchall()
    return {row[0] for row in result}
