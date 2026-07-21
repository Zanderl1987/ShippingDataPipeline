from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.seafarer_index import (
    _parse_ports,
    _parse_ships,
    collect_ports,
    collect_ships,
)


@pytest.fixture
def mock_settings(tmp_path: Path) -> None:
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    yield
    settings.data_dir = old_data
    settings.storage_dir = old_storage


MOCK_SHIPS = [
    {
        "imo": 9876543,
        "mmsi": 355000000,
        "name": "TEST CARRIER",
        "type": "Bulk Carrier",
        "flag": "PA",
        "callsign": "TEST1",
        "length": 229,
        "beam": 32,
        "gross_tonnage": 43000,
        "deadweight": 81600,
        "year_built": 2014,
    },
    {
        "imo": 1234567,
        "mmsi": 636000000,
        "name": "TEST TANKER",
        "type": "Crude Oil Tanker",
        "flag": "LR",
        "callsign": "A1B2C3",
        "length": 333,
        "beam": 60,
        "gross_tonnage": 160000,
        "deadweight": 300000,
        "year_built": 2020,
    },
]

MOCK_PORTS = [
    {
        "unlocode": "NLRTM",
        "name": "Rotterdam",
        "country": "Netherlands",
        "country_code": "NL",
        "latitude": 51.9225,
        "longitude": 4.4792,
        "timezone": "Europe/Amsterdam",
        "region": "Europe",
    },
    {
        "unlocode": "SGSIN",
        "name": "Singapore",
        "country": "Singapore",
        "country_code": "SG",
        "latitude": 1.2647,
        "longitude": 103.822,
        "timezone": "Asia/Singapore",
        "region": "Asia",
    },
]


def test_parse_ships() -> None:
    df = _parse_ships(MOCK_SHIPS)
    assert df.height == 2
    assert "imo" in df.columns
    assert "vessel_name" in df.columns
    assert "vessel_type" in df.columns
    assert "flag" in df.columns
    assert "source" in df.columns
    assert df[0, "vessel_name"] == "TEST CARRIER"
    assert df[0, "imo"] == 9876543
    assert df[0, "source"] == "seafarer_index"


def test_parse_ships_empty() -> None:
    df = _parse_ships([])
    assert df.height == 0


def test_parse_ports() -> None:
    df = _parse_ports(MOCK_PORTS)
    assert df.height == 2
    assert "unlocode" in df.columns
    assert "port_name" in df.columns
    assert "country" in df.columns
    assert "latitude" in df.columns
    assert "longitude" in df.columns
    assert "source" in df.columns
    assert df[0, "port_name"] == "Rotterdam"
    assert df[0, "unlocode"] == "NLRTM"
    assert df[0, "source"] == "seafarer_index"


def test_parse_ports_empty() -> None:
    df = _parse_ports([])
    assert df.height == 0


@patch("src.collectors.seafarer_index.write_raw")
@patch("src.collectors.seafarer_index.fetch_ships")
def test_collect_ships(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_SHIPS
    mock_write.return_value = 2

    count = collect_ships()
    assert count == 2
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()


@patch("src.collectors.seafarer_index.write_raw")
@patch("src.collectors.seafarer_index.fetch_ports")
def test_collect_ports(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_PORTS
    mock_write.return_value = 2

    count = collect_ports()
    assert count == 2
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()
