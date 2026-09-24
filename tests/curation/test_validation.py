from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from src.curation.validation import (
    validate_ais_positions,
    validate_not_null,
    validate_ports,
    validate_positive,
    validate_range,
    validate_vessels,
)
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


def test_validate_not_null_pass(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [123, 456],
            "imo": [111, 222],
            "vessel_name": ["A", "B"],
            "latitude": [51.0, 52.0],
            "longitude": [0.1, 0.2],
            "sog": [10.0, 12.0],
            "cog": [90.0, 180.0],
            "heading": [90.0, 180.0],
            "nav_status": ["Under way"] * 2,
            "draught": [5.0] * 2,
            "destination": ["PORT"] * 2,
            "eta": ["2026-01-01"] * 2,
            "timestamp": ["2026-01-01 10:00:00"] * 2,
            "source": ["test"] * 2,
            "partition_date": [date(2026, 1, 1)] * 2,
        }
    )

    write_raw("test", df)

    result = validate_not_null("ais_positions", "mmsi")
    assert result.passed
    assert result.failed_count == 0


def test_validate_not_null_fail(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [123, None],
            "imo": [111, 222],
            "vessel_name": ["A", "B"],
            "latitude": [51.0, 52.0],
            "longitude": [0.1, 0.2],
            "sog": [10.0, 12.0],
            "cog": [90.0, 180.0],
            "heading": [90.0, 180.0],
            "nav_status": ["Under way"] * 2,
            "draught": [5.0] * 2,
            "destination": ["PORT"] * 2,
            "eta": ["2026-01-01"] * 2,
            "timestamp": ["2026-01-01 10:00:00"] * 2,
            "source": ["test"] * 2,
            "partition_date": [date(2026, 1, 1)] * 2,
        }
    )

    write_raw("test", df)

    result = validate_not_null("ais_positions", "mmsi")
    assert not result.passed
    assert result.failed_count == 1


def test_validate_range_pass(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [123, 456],
            "imo": [111, 222],
            "vessel_name": ["A", "B"],
            "latitude": [51.0, 52.0],
            "longitude": [0.1, 0.2],
            "sog": [10.0, 12.0],
            "cog": [90.0, 180.0],
            "heading": [90.0, 180.0],
            "nav_status": ["Under way"] * 2,
            "draught": [5.0] * 2,
            "destination": ["PORT"] * 2,
            "eta": ["2026-01-01"] * 2,
            "timestamp": ["2026-01-01 10:00:00"] * 2,
            "source": ["test"] * 2,
            "partition_date": [date(2026, 1, 1)] * 2,
        }
    )

    write_raw("test", df)

    result = validate_range("ais_positions", "latitude", -90, 90)
    assert result.passed
    assert result.failed_count == 0


def test_validate_range_fail(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [123, 456],
            "imo": [111, 222],
            "vessel_name": ["A", "B"],
            "latitude": [51.0, 91.0],
            "longitude": [0.1, 0.2],
            "sog": [10.0, 12.0],
            "cog": [90.0, 180.0],
            "heading": [90.0, 180.0],
            "nav_status": ["Under way"] * 2,
            "draught": [5.0] * 2,
            "destination": ["PORT"] * 2,
            "eta": ["2026-01-01"] * 2,
            "timestamp": ["2026-01-01 10:00:00"] * 2,
            "source": ["test"] * 2,
            "partition_date": [date(2026, 1, 1)] * 2,
        }
    )

    write_raw("test", df)

    result = validate_range("ais_positions", "latitude", -90, 90)
    assert not result.passed
    assert result.failed_count == 1


def test_validate_positive_pass(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [123, 456],
            "imo": [111, 222],
            "vessel_name": ["A", "B"],
            "latitude": [51.0, 52.0],
            "longitude": [0.1, 0.2],
            "sog": [10.0, 12.0],
            "cog": [90.0, 180.0],
            "heading": [90.0, 180.0],
            "nav_status": ["Under way"] * 2,
            "draught": [5.0] * 2,
            "destination": ["PORT"] * 2,
            "eta": ["2026-01-01"] * 2,
            "timestamp": ["2026-01-01 10:00:00"] * 2,
            "source": ["test"] * 2,
            "partition_date": [date(2026, 1, 1)] * 2,
        }
    )

    write_raw("test", df)

    result = validate_positive("ais_positions", "sog")
    assert result.passed


def test_validate_ais_positions(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "mmsi": [123, 456],
            "imo": [111, 222],
            "vessel_name": ["A", "B"],
            "latitude": [51.0, 52.0],
            "longitude": [0.1, 0.2],
            "sog": [10.0, 12.0],
            "cog": [90.0, 180.0],
            "heading": [90.0, 180.0],
            "nav_status": ["Under way"] * 2,
            "draught": [5.0] * 2,
            "destination": ["PORT"] * 2,
            "eta": ["2026-01-01"] * 2,
            "timestamp": ["2026-01-01 10:00:00"] * 2,
            "source": ["test"] * 2,
            "partition_date": [date(2026, 1, 1)] * 2,
        }
    )

    write_raw("test", df)

    report = validate_ais_positions()
    assert report.passed
    assert report.total_checks == 7


def _ais_row(**kw: object) -> dict[str, object]:
    row: dict[str, object] = {
        "mmsi": 123,
        "imo": 111,
        "vessel_name": "A",
        "latitude": 51.0,
        "longitude": 0.1,
        "sog": 10.0,
        "cog": 90.0,
        "heading": 90.0,
        "timestamp": datetime(2026, 1, 1, 10),
        "source": "test",
        "partition_date": date(2026, 1, 1),
    }
    row.update(kw)
    return row


def test_validate_ais_positions_allows_axiomancer_without_mmsi_or_timestamp(db) -> None:
    # Axiomancer never reports mmsi or a timestamp; requiring them made this
    # check fail on every run.
    init_db()
    write_raw(
        "test",
        pl.DataFrame(
            [
                _ais_row(),
                _ais_row(mmsi=None, imo=222, vessel_name="B", timestamp=None,
                         source="axiomancer"),
            ]
        ),
    )
    report = validate_ais_positions()
    assert report.passed, [r.message for r in report.results if not r.passed]


def test_validate_ais_positions_still_requires_mmsi_for_other_sources(db) -> None:
    init_db()
    write_raw("test", pl.DataFrame([_ais_row(mmsi=None)]))
    failed = [r.check for r in validate_ais_positions().results if not r.passed]
    assert failed == ["not_null(mmsi) excluding axiomancer"]


def test_validate_ais_positions_top_encodable_speed_passes(db) -> None:
    # AIS speed tops out at 102.2 ("102.2 kn or more").
    init_db()
    write_raw("test", pl.DataFrame([_ais_row(sog=102.2)]))
    assert validate_ais_positions().passed


def test_validate_ais_positions_rejects_heading_not_available(db) -> None:
    init_db()
    write_raw("test", pl.DataFrame([_ais_row(heading=511.0)]))
    failed = [r.check for r in validate_ais_positions().results if not r.passed]
    assert failed == ["range(heading, [0, 359])"]


def test_validate_vessels(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "imo": [111, 222],
            "mmsi": [123, 456],
            "vessel_name": ["A", "B"],
            "vessel_type": ["Container", "Tanker"],
            "flag": ["PA", "SG"],
            "callsign": ["A", "B"],
            "length_m": [300.0, 200.0],
            "beam_m": [40.0, 30.0],
            "gross_tonnage": [100000.0, 50000.0],
            "deadweight_tonnage": [120000.0, 60000.0],
            "year_built": [2020, 2015],
            "owner_name": ["Owner A", "Owner B"],
            "manager_name": ["Manager A", "Manager B"],
            "source": ["test"] * 2,
        }
    )

    write_raw("test", df, table_name="vessels")

    report = validate_vessels()
    assert report.passed
    assert report.total_checks == 2


def test_validate_ports(db) -> None:
    init_db()

    df = pl.DataFrame(
        {
            "unlocode": ["NLRTM", "SGSIN"],
            "port_name": ["Rotterdam", "Singapore"],
            "country": ["Netherlands", "Singapore"],
            "country_code": ["NL", "SG"],
            "latitude": [51.9, 1.3],
            "longitude": [4.5, 103.8],
            "timezone": ["Europe/Amsterdam", "Asia/Singapore"],
            "region": ["Europe", "Asia"],
            "source": ["test"] * 2,
        }
    )

    write_raw("test", df, table_name="ports")

    report = validate_ports()
    assert report.passed
    assert report.total_checks == 3
