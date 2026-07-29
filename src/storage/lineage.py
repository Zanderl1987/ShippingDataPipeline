"""Data lineage tracking for the shipping pipeline."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any

import duckdb

from src.storage.writer import get_connection

LINEAGE_TABLES_SQL = [
    "CREATE SEQUENCE IF NOT EXISTS lineage_event_id_seq START 1;",
    """
    CREATE TABLE IF NOT EXISTS lineage_events (
        event_id        INTEGER,
        event_type      VARCHAR NOT NULL,
        source          VARCHAR NOT NULL,
        started_at      TIMESTAMP NOT NULL,
        completed_at    TIMESTAMP,
        status          VARCHAR DEFAULT 'success',
        rows_input      INTEGER DEFAULT 0,
        rows_output     INTEGER DEFAULT 0,
        duration_ms     INTEGER,
        version         VARCHAR,
        config_hash     VARCHAR,
        error_message   VARCHAR,
        metadata        VARCHAR
    );
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_lineage_source ON lineage_events(source);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_lineage_type ON lineage_events(event_type);
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_lineage_time ON lineage_events(started_at);
    """,
]


def ensure_lineage_tables() -> None:
    conn = get_connection()
    try:
        for sql in LINEAGE_TABLES_SQL:
            conn.execute(sql)
    finally:
        conn.close()


def _hash_config(config: dict[str, Any]) -> str:
    serialized = json.dumps(config, sort_keys=True, default=str)
    return hashlib.sha256(serialized.encode()).hexdigest()[:16]


class LineageTracker:
    """Track data lineage across collection, curation, and analytics."""

    def __init__(self) -> None:
        self._conn: duckdb.DuckDBPyConnection | None = None
        ensure_lineage_tables()

    def __enter__(self) -> LineageTracker:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _get_conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = get_connection()
        return self._conn

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def record_event(
        self,
        event_type: str,
        source: str,
        started_at: datetime,
        completed_at: datetime | None = None,
        status: str = "success",
        rows_input: int = 0,
        rows_output: int = 0,
        duration_ms: int | None = None,
        version: str | None = None,
        config: dict[str, Any] | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        conn = self._get_conn()
        config_hash = _hash_config(config) if config else None
        metadata_json = json.dumps(metadata, default=str) if metadata else None

        conn.execute(
            """
            INSERT INTO lineage_events
            (event_id, event_type, source, started_at, completed_at, status,
             rows_input, rows_output, duration_ms, version, config_hash,
             error_message, metadata)
            VALUES (nextval('lineage_event_id_seq'), ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                event_type, source, started_at, completed_at, status,
                rows_input, rows_output, duration_ms, version, config_hash,
                error_message, metadata_json,
            ],
        )
        row_id = conn.execute("SELECT currval('lineage_event_id_seq')").fetchone()
        return row_id[0] if row_id else 0

    def update_event(
        self,
        event_id: int,
        *,
        completed_at: datetime | None = None,
        status: str | None = None,
        rows_output: int | None = None,
        duration_ms: int | None = None,
        error_message: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        conn = self._get_conn()
        updates: list[str] = []
        params: list[Any] = []

        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)
        if status is not None:
            updates.append("status = ?")
            params.append(status)
        if rows_output is not None:
            updates.append("rows_output = ?")
            params.append(rows_output)
        if duration_ms is not None:
            updates.append("duration_ms = ?")
            params.append(duration_ms)
        if error_message is not None:
            updates.append("error_message = ?")
            params.append(error_message)
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata, default=str))

        if not updates:
            return

        params.append(event_id)
        conn.execute(
            f"UPDATE lineage_events SET {', '.join(updates)} WHERE event_id = ?",
            params,
        )

    def get_lineage(
        self,
        source: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> duckdb.DuckDBPyConnection:
        conn = self._get_conn()
        conditions = []
        params: list[Any] = []
        if source:
            conditions.append("source = ?")
            params.append(source)
        if event_type:
            conditions.append("event_type = ?")
            params.append(event_type)

        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)

        return conn.execute(
            f"""
            SELECT event_id, event_type, source, started_at, completed_at,
                   status, rows_input, rows_output, duration_ms, version,
                   error_message
            FROM lineage_events
            {where}
            ORDER BY started_at DESC
            LIMIT ?
            """,
            params,
        ).pl()

    def get_lineage_summary(self) -> duckdb.DuckDBPyConnection:
        conn = self._get_conn()
        return conn.execute(
            """
            SELECT source, event_type,
                   COUNT(*) as total_runs,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successes,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END) as failures,
                   SUM(rows_output) as total_rows,
                   MIN(started_at) as first_run,
                   MAX(started_at) as last_run
            FROM lineage_events
            GROUP BY source, event_type
            ORDER BY last_run DESC
            """
        ).pl()


def record_analytics_run(
    module: str,
    started_at: datetime,
    *,
    rows_output: int = 0,
    duration_ms: int | None = None,
    params: dict[str, Any] | None = None,
) -> int | None:
    from src.analytics import ANALYTICS_VERSIONS
    tracker = LineageTracker()
    try:
        return tracker.record_event(
            event_type="analytics",
            source=module,
            started_at=started_at,
            rows_output=rows_output,
            duration_ms=duration_ms,
            version=ANALYTICS_VERSIONS.get(module),
            config=params,
        )
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Analytics lineage failed: %s", e)
        return None
    finally:
        tracker.close()
