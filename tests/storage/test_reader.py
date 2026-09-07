"""Reader guards for tables that lack the columns some functions used to
hardcode. Both bugs previously produced an opaque DuckDB binder error
("Referenced column not found") instead of a clear, actionable message.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from src.storage.reader import get_latest_timestamp, read_dataset
from src.storage.writer import init_db, write_raw


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


def test_get_latest_timestamp_on_table_without_timestamp_column_raises_clear_error(db) -> None:
    """vessels has no `timestamp` column -- must not hit DuckDB's binder error."""
    with pytest.raises(ValueError, match="timestamp"):
        get_latest_timestamp("vessels", source="test")


def test_get_latest_timestamp_works_on_table_with_timestamp_column(db) -> None:
    df = pl.DataFrame(
        {
            "mmsi": [123],
            "imo": [111],
            "vessel_name": ["A"],
            "latitude": [51.0],
            "longitude": [0.1],
            "sog": [10.0],
            "cog": [90.0],
            "heading": [90.0],
            "nav_status": ["Under way"],
            "draught": [5.0],
            "destination": ["PORT"],
            "eta": ["2026-01-01"],
            "timestamp": ["2026-01-01 10:00:00"],
            "source": ["test"],
            "partition_date": [date(2026, 1, 1)],
        }
    )
    write_raw("test", df)
    ts = get_latest_timestamp("ais_positions", source="test")
    assert ts is not None
    assert ts.year == 2026


def test_read_dataset_on_table_without_partition_date_raises_clear_error(db) -> None:
    """trade_flow is partitioned by (year, reporter_code), not partition_date --
    date_from/date_to filtering must not silently emit a wrong-column filter
    or hit DuckDB's binder error."""
    with pytest.raises(ValueError, match="partition_date"):
        read_dataset("trade_flow", date_from=date(2026, 1, 1))


def test_read_dataset_without_date_filters_still_works_on_trade_flow(db) -> None:
    """No date_from/date_to given -- the guard must not fire needlessly."""
    out = read_dataset("trade_flow")
    assert out.height == 0


def test_read_dataset_date_filter_still_works_on_partition_date_tables(db) -> None:
    df = pl.DataFrame(
        {
            "mmsi": [123],
            "imo": [111],
            "vessel_name": ["A"],
            "latitude": [51.0],
            "longitude": [0.1],
            "sog": [10.0],
            "cog": [90.0],
            "heading": [90.0],
            "nav_status": ["Under way"],
            "draught": [5.0],
            "destination": ["PORT"],
            "eta": ["2026-01-01"],
            "timestamp": ["2026-01-01 10:00:00"],
            "source": ["test"],
            "partition_date": [date(2026, 1, 1)],
        }
    )
    write_raw("test", df)
    out = read_dataset("ais_positions", date_from=date(2026, 1, 1), date_to=date(2026, 1, 1))
    assert out.height == 1
