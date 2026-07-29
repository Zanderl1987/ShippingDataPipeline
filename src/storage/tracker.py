"""Source tracking for collection events and staleness."""
from __future__ import annotations

import time
from datetime import datetime, timedelta
from typing import Any

import duckdb
import polars as pl

from src.storage.writer import get_connection


class SourceTracker:
    """Track collection events for each data source.

    Records when data was last fetched, how many rows were written,
    and whether the collection succeeded or failed.
    """

    def __init__(self) -> None:
        self._conn: duckdb.DuckDBPyConnection | None = None

    def __enter__(self) -> SourceTracker:
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

    def record_collection(
        self,
        source: str,
        rows_fetched: int,
        rows_written: int,
        status: str = "success",
        error_message: str | None = None,
        duration_ms: int | None = None,
    ) -> None:
        """Record a collection event.

        Args:
            source: Source identifier (e.g. "axiomancer", "seafarer_index").
            rows_fetched: Number of rows fetched from the API.
            rows_written: Number of rows written to storage.
            status: "success" or "error".
            error_message: Error message if status is "error".
            duration_ms: Collection duration in milliseconds.
        """
        conn = self._get_conn()
        conn.execute(
            """
            INSERT INTO source_tracking
            (source, collection_ts, rows_fetched, rows_written, status,
             error_message, duration_ms)
            VALUES (?, now(), ?, ?, ?, ?, ?)
            """,
            [source, rows_fetched, rows_written, status, error_message,
             duration_ms],
        )

    def get_last_collection(self, source: str) -> dict[str, Any] | None:
        """Get the most recent successful collection for a source.

        Returns dict with keys: collection_ts, rows_fetched, rows_written,
        duration_ms. Returns None if no successful collection exists.
        """
        conn = self._get_conn()
        result = conn.execute(
            """
            SELECT collection_ts, rows_fetched, rows_written, duration_ms
            FROM source_tracking
            WHERE source = ? AND status = 'success'
            ORDER BY collection_ts DESC
            LIMIT 1
            """,
            [source],
        ).fetchone()

        if result is None:
            return None

        return {
            "collection_ts": result[0],
            "rows_fetched": result[1],
            "rows_written": result[2],
            "duration_ms": result[3],
        }

    def get_collection_history(
        self, source: str, limit: int = 10
    ) -> pl.DataFrame:
        """Get recent collection history for a source."""
        conn = self._get_conn()
        return conn.execute(
            """
            SELECT collection_ts, rows_fetched, rows_written, status,
                   error_message, duration_ms
            FROM source_tracking
            WHERE source = ?
            ORDER BY collection_ts DESC
            LIMIT ?
            """,
            [source, limit],
        ).pl()

    def get_all_sources_status(self) -> pl.DataFrame:
        """Get last successful collection time for all sources."""
        conn = self._get_conn()
        return conn.execute(
            """
            SELECT source,
                   MAX(collection_ts) as last_collection,
                   SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END)
                       as total_successes,
                   SUM(CASE WHEN status = 'error' THEN 1 ELSE 0 END)
                       as total_errors
            FROM source_tracking
            GROUP BY source
            ORDER BY last_collection DESC
            """
        ).pl()

    def get_staleness_hours(self, source: str) -> float | None:
        """Get hours since last successful collection.

        Returns None if no successful collection exists.
        """
        last = self.get_last_collection(source)
        if last is None:
            return None

        now = datetime.now()
        last_ts = last["collection_ts"]
        if isinstance(last_ts, str):
            last_ts = datetime.fromisoformat(last_ts)

        delta = now - last_ts
        result: float = delta.total_seconds() / 3600
        return result

    def record_lineage(
        self,
        source: str,
        event_type: str,
        started_at: datetime,
        *,
        rows_input: int = 0,
        rows_output: int = 0,
        duration_ms: int | None = None,
        version: str | None = None,
        config: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        from src.storage.lineage import LineageTracker
        tracker = LineageTracker()
        try:
            return tracker.record_event(
                event_type=event_type,
                source=source,
                started_at=started_at,
                rows_input=rows_input,
                rows_output=rows_output,
                duration_ms=duration_ms,
                version=version,
                config=config,
                metadata=metadata,
            )
        finally:
            tracker.close()


class TimedCollector:
    """Context manager that times a collection and records it to SourceTracker."""

    def __init__(
        self,
        tracker: SourceTracker,
        source: str,
    ) -> None:
        self.tracker = tracker
        self.source = source
        self.rows_fetched: int = 0
        self.rows_written: int = 0
        self._start_time: float = 0
        self._error: str | None = None

    def __enter__(self) -> TimedCollector:
        self._start_time = time.perf_counter()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        duration_ms = int((time.perf_counter() - self._start_time) * 1000)

        if exc_val is not None:
            self._error = str(exc_val)
            status = "error"
        else:
            status = "success"

        self.tracker.record_collection(
            source=self.source,
            rows_fetched=self.rows_fetched,
            rows_written=self.rows_written,
            status=status,
            error_message=self._error,
            duration_ms=duration_ms,
        )

        from src.storage.lineage import LineageTracker
        lineage = LineageTracker()
        try:
            lineage.record_event(
                event_type="collection",
                source=self.source,
                started_at=datetime.now() - timedelta(milliseconds=duration_ms),
                completed_at=datetime.now(),
                status=status,
                rows_input=self.rows_fetched,
                rows_output=self.rows_written,
                duration_ms=duration_ms,
                error_message=self._error,
            )
        finally:
            lineage.close()
