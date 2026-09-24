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


# --- 2026-09-24: curation validation failed on every run ---
#
# AIS "not available" codes (sog 102.3, cog 360, heading 511) were stored as
# real values, and the not-null checks demanded mmsi/timestamp from
# axiomancer, which never sends them. Cleaning now runs before validation.

def test_run_curation_cleans_before_validating(db) -> None:
    from datetime import date, datetime

    import polars as pl

    from src.storage.writer import write_raw

    write_raw(
        "digitraffic",
        pl.DataFrame(
            {
                "mmsi": [1, 2, None],
                "vessel_name": ["A", "B", "C"],
                "latitude": [51.0, 51.0, 51.0],
                "longitude": [4.0, 4.0, 4.0],
                "sog": [102.3, 10.0, 10.0],
                "cog": [360.0, 90.0, 90.0],
                "heading": [511.0, 90.0, 90.0],
                "timestamp": [datetime(2026, 9, 24, h) for h in (1, 2, 3)],
                "source": ["digitraffic"] * 3,
                "partition_date": [date(2026, 9, 24)] * 3,
            }
        ),
    )
    write_raw(
        "axiomancer",
        pl.DataFrame(
            {
                "mmsi": [None],
                "imo": [9000001],
                "vessel_name": ["D"],
                "latitude": [10.0],
                "longitude": [10.0],
                "source": ["axiomancer"],
                "partition_date": [date(2026, 9, 24)],
            },
            schema_overrides={"mmsi": pl.Int64},
        ),
    )

    result = p.run_curation(skip_enrichment=True)

    assert result.errors == []
    assert result.cleaning_results["ais_positions"] == 4  # 3 codes + 1 row
    failed = [
        (r.table, x.check)
        for r in result.validation_reports
        for x in r.results
        if not x.passed
    ]
    assert failed == []
