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
from src.storage.writer import get_connection, init_db, write_raw


def _plant_duplicate(table_name: str, where: str) -> None:
    """Copy an existing row so the table holds a duplicate.

    `write_raw` now dedups on the natural key, so duplicates can only come from
    a DB written before that landed. These functions exist to clean those up.
    """
    conn = get_connection()
    try:
        conn.execute(f"INSERT INTO {table_name} BY NAME SELECT * FROM {table_name} WHERE {where}")
    finally:
        conn.close()


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
    _plant_duplicate("ais_positions", "mmsi = 123")

    removed = deduplicate_ais_positions()
    assert removed == 1


# --- 2026-09-07 code review fix ---

def test_deduplicate_ais_positions_keeps_axiomancer_null_mmsi_rows(db) -> None:
    """Fixed: dedup used to GROUP BY mmsi, timestamp, source only. Axiomancer
    reports no mmsi/timestamp at all, so SQL's NULL=NULL GROUP BY semantics
    collapsed its ENTIRE multi-day history into one row -- schema.py's
    AIS_POSITIONS.dedup_keys documents the 6-column key needed (adding imo,
    vessel_name, partition_date) to keep distinct axiomancer snapshots apart."""
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [None, None, None],
            "imo": [111, 222, 111],
            "vessel_name": ["Ship A", "Ship B", "Ship A"],
            "latitude": [51.0, 52.0, 51.1],
            "longitude": [0.1, 0.2, 0.11],
            "sog": [None, None, None],
            "cog": [None, None, None],
            "heading": [None, None, None],
            "nav_status": [None, None, None],
            "draught": [None, None, None],
            "destination": [None, None, None],
            "eta": [None, None, None],
            "timestamp": [None, None, None],
            "source": ["axiomancer", "axiomancer", "axiomancer"],
            "partition_date": [date(2026, 1, 1), date(2026, 1, 1), date(2026, 1, 2)],
        }
    )

    write_raw("test", df)

    count_before = query("SELECT count(*) as cnt FROM ais_positions")[0, "cnt"]
    assert count_before == 3, "distinct axiomancer rows must not collide on write"

    removed = deduplicate_ais_positions()
    assert removed == 0, "no two rows here actually share the full dedup key"

    count_after = query("SELECT count(*) as cnt FROM ais_positions")[0, "cnt"]
    assert count_after == 3


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
    _plant_duplicate("vessels", "imo = 111")

    count_df = query("SELECT count(*) as cnt FROM vessels")
    assert count_df[0, "cnt"] == 3

    removed = deduplicate_vessels()
    assert removed == 1

    count_df2 = query("SELECT count(*) as cnt FROM vessels")
    assert count_df2[0, "cnt"] == 2


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
    _plant_duplicate("ais_positions", "mmsi = 123")

    removed = deduplicate_table("ais_positions", ["mmsi", "timestamp", "source"])
    assert removed == 1
