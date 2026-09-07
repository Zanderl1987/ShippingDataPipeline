from __future__ import annotations

from pathlib import Path

import duckdb
import pytest

from src.curation import pipeline as p
from src.storage.writer import init_db


@pytest.fixture
def db(tmp_path: Path):
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    init_db().close()

    yield

    settings.data_dir = old_data
    settings.storage_dir = old_storage


# --- 2026-09-07 code review fix ---
#
# run_deduplication()/run_enrichment() logged per-table failures but never
# recorded them anywhere the caller could see -- CurationResult.errors stayed
# empty even when a table's dedup/enrichment silently never ran, so a caller
# checking `if result.errors` to decide whether a curation run is trustworthy
# would wrongly conclude success.

def test_run_deduplication_records_failure_in_errors_list(db, monkeypatch) -> None:
    def boom(conn):
        raise RuntimeError("dedup exploded")

    monkeypatch.setattr(p, "deduplicate_ais_positions", boom)
    errors: list[str] = []
    conn = duckdb.connect(":memory:")
    try:
        results = p.run_deduplication(conn, errors=errors)
    finally:
        conn.close()

    assert "ais_positions" not in results
    assert any("ais_positions" in e and "dedup exploded" in e for e in errors)


def test_run_enrichment_records_failure_in_errors_list(db, monkeypatch) -> None:
    def boom(conn):
        raise RuntimeError("enrichment exploded")

    monkeypatch.setattr(p, "create_curated_ais_positions", boom)
    errors: list[str] = []
    conn = duckdb.connect(":memory:")
    try:
        results = p.run_enrichment(conn, errors=errors)
    finally:
        conn.close()

    assert "curated_ais_positions" not in results
    assert any("ais_positions" in e and "enrichment exploded" in e for e in errors)


def test_run_curation_end_to_end_surfaces_dedup_failure(db, monkeypatch) -> None:
    """The full run_curation() orchestrator must thread errors= through, not
    just the individual run_deduplication/run_enrichment functions."""

    def boom(conn):
        raise RuntimeError("dedup exploded")

    monkeypatch.setattr(p, "deduplicate_ais_positions", boom)
    result = p.run_curation()
    assert any("dedup exploded" in e for e in result.errors)


def test_run_deduplication_without_errors_list_does_not_crash(db) -> None:
    """errors=None (the default) must keep working -- failures are still
    logged, just not collected."""
    conn = duckdb.connect(":memory:")
    try:
        results = p.run_deduplication(conn)
    finally:
        conn.close()
    assert isinstance(results, dict)
