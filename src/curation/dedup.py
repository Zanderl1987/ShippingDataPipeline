from __future__ import annotations

import logging

import duckdb

from src.storage.writer import get_db_path

logger = logging.getLogger(__name__)


def _get_count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    """Execute a count query and return the result."""
    row = conn.execute(sql).fetchone()
    assert row is not None
    result: int = row[0]
    return result


def deduplicate_ais_positions(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Remove duplicate AIS positions based on mmsi + timestamp.

    Returns number of rows removed.
    """
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        count_before = _get_count(conn, "SELECT count(*) FROM ais_positions")

        conn.execute("""
            DELETE FROM ais_positions
            WHERE rowid NOT IN (
                SELECT MIN(rowid)
                FROM ais_positions
                GROUP BY mmsi, timestamp, source
            )
        """)

        count_after = _get_count(conn, "SELECT count(*) FROM ais_positions")

        removed = count_before - count_after
        if removed > 0:
            logger.info("Deduplicated ais_positions: removed %d rows", removed)
        return removed
    finally:
        if should_close:
            conn.close()


def deduplicate_vessels(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Remove duplicate vessels based on imo (primary key).

    Returns number of rows removed.
    """
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        count_before = _get_count(conn, "SELECT count(*) FROM vessels")

        conn.execute("""
            DELETE FROM vessels
            WHERE rowid NOT IN (
                SELECT MAX(rowid)
                FROM vessels
                GROUP BY imo
            )
        """)

        count_after = _get_count(conn, "SELECT count(*) FROM vessels")

        removed = count_before - count_after
        if removed > 0:
            logger.info("Deduplicated vessels: removed %d rows", removed)
        return removed
    finally:
        if should_close:
            conn.close()


def deduplicate_ports(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Remove duplicate ports based on unlocode (primary key).

    Returns number of rows removed.
    """
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        count_before = _get_count(conn, "SELECT count(*) FROM ports")

        conn.execute("""
            DELETE FROM ports
            WHERE rowid NOT IN (
                SELECT MAX(rowid)
                FROM ports
                GROUP BY unlocode
            )
        """)

        count_after = _get_count(conn, "SELECT count(*) FROM ports")

        removed = count_before - count_after
        if removed > 0:
            logger.info("Deduplicated ports: removed %d rows", removed)
        return removed
    finally:
        if should_close:
            conn.close()


def deduplicate_table(
    table_name: str,
    key_columns: list[str],
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Generic deduplication for any table.

    Args:
        table_name: Name of the table to deduplicate.
        key_columns: Columns to use for deduplication.
        conn: Optional DuckDB connection.

    Returns number of rows removed.
    """
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        count_before = _get_count(conn, f"SELECT count(*) FROM {table_name}")

        key_cols = ", ".join(key_columns)
        conn.execute(f"""
            DELETE FROM {table_name}
            WHERE rowid NOT IN (
                SELECT MIN(rowid)
                FROM {table_name}
                GROUP BY {key_cols}
            )
        """)

        count_after = _get_count(conn, f"SELECT count(*) FROM {table_name}")

        removed = count_before - count_after
        if removed > 0:
            logger.info("Deduplicated %s: removed %d rows", table_name, removed)
        return removed
    finally:
        if should_close:
            conn.close()
