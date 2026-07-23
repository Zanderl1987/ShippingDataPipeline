from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from src.curation.dedup import (
    deduplicate_ais_positions,
    deduplicate_ports,
    deduplicate_table,
    deduplicate_vessels,
)
from src.storage.reader import query
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


def test_deduplicate_ais_positions(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [123, 123, 456],
            "imo": [111, 111, 222],
            "vessel_name": ["A", "A", "B"],
            "latitude": [51.0, 51.0, 52.0],
            "longitude": [0.1, 0.1, 0.2],
            "sog": [10.0, 10.0, 12.0],
            "cog": [90.0, 90.0, 180.0],
            "heading": [90.0, 90.0, 180.0],
            "nav_status": ["Under way"] * 3,
            "draught": [5.0] * 3,
            "destination": ["PORT"] * 3,
            "eta": ["2026-01-01"] * 3,
            "timestamp": ["2026-01-01 10:00:00", "2026-01-01 10:00:00", "2026-01-01 11:00:00"],
            "source": ["test"] * 3,
            "partition_date": [date(2026, 1, 1)] * 3,
        }
    )

    write_raw("test", df)

    removed = deduplicate_ais_positions()
    assert removed == 1


def test_deduplicate_vessels(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "imo": [111, 111, 222],
            "mmsi": [123, 123, 456],
            "vessel_name": ["A", "A", "B"],
            "vessel_type": ["Container", "Container", "Tanker"],
            "flag": ["PA", "PA", "SG"],
            "callsign": ["A", "A", "B"],
            "length_m": [300.0, 300.0, 200.0],
            "beam_m": [40.0, 40.0, 30.0],
            "gross_tonnage": [100000.0, 100000.0, 50000.0],
            "deadweight_tonnage": [120000.0, 120000.0, 60000.0],
            "year_built": [2020, 2020, 2015],
            "owner_name": ["Owner A", "Owner A", "Owner B"],
            "manager_name": ["Manager A", "Manager A", "Manager B"],
            "source": ["test"] * 3,
        }
    )

    write_raw("test", df, table_name="vessels")

    count_df = query("SELECT count(*) as cnt FROM vessels")
    assert count_df[0, "cnt"] == 2

    removed = deduplicate_vessels()
    assert removed == 0


def test_deduplicate_ports(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "unlocode": ["NLRTM", "NLRTM", "SGSIN"],
            "port_name": ["Rotterdam", "Rotterdam", "Singapore"],
            "country": ["Netherlands", "Netherlands", "Singapore"],
            "country_code": ["NL", "NL", "SG"],
            "latitude": [51.9, 51.9, 1.3],
            "longitude": [4.5, 4.5, 103.8],
            "timezone": ["Europe/Amsterdam"] * 3,
            "region": ["Europe", "Europe", "Asia"],
            "source": ["test"] * 3,
        }
    )

    write_raw("test", df, table_name="ports")

    count_df = query("SELECT count(*) as cnt FROM ports")
    assert count_df[0, "cnt"] == 2

    removed = deduplicate_ports()
    assert removed == 0


def test_deduplicate_table(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [123, 123, 456],
            "imo": [111, 111, 222],
            "vessel_name": ["A", "A", "B"],
            "latitude": [51.0, 51.0, 52.0],
            "longitude": [0.1, 0.1, 0.2],
            "sog": [10.0, 10.0, 12.0],
            "cog": [90.0, 90.0, 180.0],
            "heading": [90.0, 90.0, 180.0],
            "nav_status": ["Under way"] * 3,
            "draught": [5.0] * 3,
            "destination": ["PORT"] * 3,
            "eta": ["2026-01-01"] * 3,
            "timestamp": ["2026-01-01 10:00:00", "2026-01-01 10:00:00", "2026-01-01 11:00:00"],
            "source": ["test"] * 3,
            "partition_date": [date(2026, 1, 1)] * 3,
        }
    )

    write_raw("test", df)

    removed = deduplicate_table("ais_positions", ["mmsi", "timestamp", "source"])
    assert removed == 1
