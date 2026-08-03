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
        "flag_iso3": "PAN",
        "gross_tonnage": 43000,
        "dwt": 81600,
        "year_built": 2014,
        "owner_slug": "test-owner",
        "manager_slug": "test-manager",
        "dimensions": {"loa_m": 229, "beam_m": 32, "draft_m": 14},
    },
    {
        "imo": 1234567,
        "mmsi": 636000000,
        "name": "TEST TANKER",
        "type": "Crude Oil Tanker",
        "flag_iso3": "LBR",
        "gross_tonnage": 160000,
        "dwt": 300000,
        "year_built": 2020,
        "owner_slug": "test-owner-2",
        "manager_slug": "test-manager-2",
        "dimensions": {"loa_m": 333, "beam_m": 60, "draft_m": 22},
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
    assert "length_m" in df.columns
    assert "beam_m" in df.columns
    assert "deadweight_tonnage" in df.columns
    assert "owner_name" in df.columns
    assert "manager_name" in df.columns
    assert "source" in df.columns
    assert df[0, "vessel_name"] == "TEST CARRIER"
    assert df[0, "imo"] == 9876543
    assert df[0, "flag"] == "PAN"
    assert df[0, "length_m"] == 229
    assert df[0, "beam_m"] == 32
    assert df[0, "deadweight_tonnage"] == 81600
    assert df[0, "owner_name"] == "test-owner"
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
