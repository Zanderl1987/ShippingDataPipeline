from __future__ import annotations

from pathlib import Path

import polars as pl
import pytest

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import init_db


@pytest.fixture
def db(tmp_path: Path) -> None:
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()

    yield

    settings.data_dir = old_data
    settings.storage_dir = old_storage


@pytest.fixture
def tracker(db) -> SourceTracker:
    init_db()
    return SourceTracker()


def test_record_collection(tracker: SourceTracker) -> None:
    tracker.record_collection(
        source="test_source",
        rows_fetched=100,
        rows_written=100,
        status="success",
        duration_ms=500,
    )

    last = tracker.get_last_collection("test_source")
    assert last is not None
    assert last["rows_fetched"] == 100
    assert last["rows_written"] == 100
    assert last["duration_ms"] == 500


def test_record_collection_error(tracker: SourceTracker) -> None:
    tracker.record_collection(
        source="test_source",
        rows_fetched=0,
        rows_written=0,
        status="error",
        error_message="API timeout",
        duration_ms=30000,
    )

    last = tracker.get_last_collection("test_source")
    assert last is None

    history = tracker.get_collection_history("test_source")
    assert history.height == 1
    assert history[0, "status"] == "error"
    assert history[0, "error_message"] == "API timeout"


def test_get_last_collection_success_only(tracker: SourceTracker) -> None:
    tracker.record_collection(
        source="test_source",
        rows_fetched=50,
        rows_written=50,
        status="error",
    )
    tracker.record_collection(
        source="test_source",
        rows_fetched=100,
        rows_written=100,
        status="success",
    )

    last = tracker.get_last_collection("test_source")
    assert last is not None
    assert last["rows_fetched"] == 100


def test_get_collection_history(tracker: SourceTracker) -> None:
    for i in range(5):
        tracker.record_collection(
            source="test_source",
            rows_fetched=i * 10,
            rows_written=i * 10,
            status="success",
        )

    history = tracker.get_collection_history("test_source", limit=3)
    assert history.height == 3

    rows = history["rows_fetched"].to_list()
    assert rows == [40, 30, 20]


def test_get_all_sources_status(tracker: SourceTracker) -> None:
    tracker.record_collection(
        source="source_a",
        rows_fetched=100,
        rows_written=100,
        status="success",
    )
    tracker.record_collection(
        source="source_b",
        rows_fetched=50,
        rows_written=50,
        status="success",
    )
    tracker.record_collection(
        source="source_b",
        rows_fetched=0,
        rows_written=0,
        status="error",
    )

    status = tracker.get_all_sources_status()
    assert status.height == 2

    source_b = status.filter(pl.col("source") == "source_b")
    assert source_b[0, "total_successes"] == 1
    assert source_b[0, "total_errors"] == 1


def test_get_staleness_hours(tracker: SourceTracker) -> None:
    assert tracker.get_staleness_hours("nonexistent") is None

    tracker.record_collection(
        source="test_source",
        rows_fetched=100,
        rows_written=100,
        status="success",
    )

    staleness = tracker.get_staleness_hours("test_source")
    assert staleness is not None
    assert staleness >= 0
    assert staleness < 1


def test_timed_collector_success(tracker: SourceTracker) -> None:
    with TimedCollector(tracker, "timed_test") as tc:
        tc.rows_fetched = 50
        tc.rows_written = 50

    last = tracker.get_last_collection("timed_test")
    assert last is not None
    assert last["rows_fetched"] == 50
    assert last["rows_written"] == 50
    assert last["duration_ms"] >= 0


def test_timed_collector_error(tracker: SourceTracker) -> None:
    with pytest.raises(ValueError):
        with TimedCollector(tracker, "timed_error_test") as tc:
            tc.rows_fetched = 10
            raise ValueError("test error")

    last = tracker.get_last_collection("timed_error_test")
    assert last is None

    history = tracker.get_collection_history("timed_error_test")
    assert history.height == 1
    assert history[0, "status"] == "error"
    assert "test error" in str(history[0, "error_message"])


def test_tracker_close(db) -> None:
    init_db()
    tracker = SourceTracker()
    conn = tracker._get_conn()
    assert conn is not None
    tracker.close()
    assert tracker._conn is None
