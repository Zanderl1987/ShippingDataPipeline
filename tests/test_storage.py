from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from src.storage.reader import list_sources, query, read_dataset
from src.storage.schema import ALL_TABLES
from src.storage.writer import init_db, write_raw


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


def test_init_db(db) -> None:
    conn = init_db()
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    ).fetchall()
    table_names = {r[0] for r in tables}
    for t in ALL_TABLES:
        assert t.name in table_names, f"Missing table: {t.name}"
    conn.close()


def test_write_and_read_ais(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [123456789, 987654321],
            "imo": [1234567, 7654321],
            "vessel_name": ["TEST VESSEL A", "TEST VESSEL B"],
            "latitude": [51.5, 52.0],
            "longitude": [0.12, -1.5],
            "sog": [12.5, 0.0],
            "cog": [180.0, 0.0],
            "heading": [180.0, 0.0],
            "nav_status": ["Under way using engine", "At anchor"],
            "draught": [10.5, 8.0],
            "destination": ["NLRTM", "GBLON"],
            "eta": ["2026-01-01 12:00:00", "2026-01-02 00:00:00"],
            "timestamp": ["2026-01-01 10:00:00", "2026-01-01 11:00:00"],
            "source": ["test", "test"],
            "partition_date": [date(2026, 1, 1), date(2026, 1, 1)],
        }
    )

    count = write_raw("test", df)
    assert count == 2

    result = read_dataset("ais_positions", source="test")
    assert result.height == 2
    assert set(result["vessel_name"].to_list()) == {
        "TEST VESSEL A",
        "TEST VESSEL B",
    }


def test_write_and_read_custom_schema(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "imo": [9999999],
            "mmsi": [999999999],
            "vessel_name": ["CUSTOM TEST"],
            "vessel_type": ["Container Ship"],
            "flag": ["PA"],
            "callsign": ["TEST1"],
            "length_m": [300.0],
            "beam_m": [40.0],
            "gross_tonnage": [100000.0],
            "deadweight_tonnage": [120000.0],
            "year_built": [2020],
            "owner_name": ["Test Owner"],
            "manager_name": ["Test Manager"],
            "source": ["test"],
        }
    )

    count = write_raw("test", df, table_name="vessels")
    assert count == 1

    result = query("SELECT * FROM vessels WHERE imo = 9999999")
    assert result.height == 1
    assert result[0, "vessel_type"] == "Container Ship"


def test_list_sources_empty(db) -> None:
    sources = list_sources()
    assert sources.height == 0


def test_read_with_date_filter(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [111, 222, 333],
            "imo": [1111111, 2222222, 3333333],
            "vessel_name": ["A", "B", "C"],
            "latitude": [1.0, 2.0, 3.0],
            "longitude": [1.0, 2.0, 3.0],
            "sog": [10.0, 10.0, 10.0],
            "cog": [90.0, 90.0, 90.0],
            "heading": [90.0, 90.0, 90.0],
            "nav_status": ["Under way"] * 3,
            "draught": [5.0] * 3,
            "destination": ["PORT"] * 3,
            "eta": ["2026-01-01"] * 3,
            "timestamp": ["2026-01-01", "2026-01-02", "2026-01-03"],
            "source": ["test"] * 3,
            "partition_date": [
                date(2026, 1, 1),
                date(2026, 1, 2),
                date(2026, 1, 3),
            ],
        }
    )
    write_raw("test", df)

    result = read_dataset(
        "ais_positions",
        date_from=date(2026, 1, 2),
        date_to=date(2026, 1, 3),
    )
    assert result.height == 2
