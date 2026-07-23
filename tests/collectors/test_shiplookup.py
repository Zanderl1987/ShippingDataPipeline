from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.shiplookup import (
    _parse_ship_detail,
    _parse_ship_search,
    collect_ship_by_imo,
    collect_ship_search,
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


MOCK_SEARCH_RESPONSE = {
    "success": True,
    "data": [
        {
            "imo": 9876543,
            "mmsi": 123456789,
            "name": "TEST CARRIER",
            "vesselName": "TEST CARRIER",
            "callsign": "H3RC",
            "shipType": "Container Ship",
            "flag": "Panama",
            "length": 366,
            "beam": 51,
            "grossTonnage": 150000,
            "yearBuilt": 2018,
        }
    ],
    "meta": {"creditsUsed": 0, "creditsRemaining": 1000},
}

MOCK_SHIP_DETAIL_RESPONSE = {
    "success": True,
    "data": {
        "imo": 9876543,
        "mmsi": 123456789,
        "name": "TEST CARRIER",
        "vesselName": "TEST CARRIER",
        "callsign": "H3RC",
        "shipType": "Container Ship",
        "flag": "Panama",
        "length": 366,
        "beam": 51,
        "grossTonnage": 150000,
        "yearBuilt": 2018,
    },
    "meta": {"creditsUsed": 1, "creditsRemaining": 999},
}


def test_parse_ship_search() -> None:
    df = _parse_ship_search(MOCK_SEARCH_RESPONSE)
    assert df.height == 1
    assert "imo" in df.columns
    assert "vessel_name" in df.columns
    assert "vessel_type" in df.columns
    assert df[0, "vessel_name"] == "TEST CARRIER"
    assert df[0, "imo"] == 9876543
    assert df[0, "flag"] == "Panama"


def test_parse_ship_search_empty() -> None:
    df = _parse_ship_search({"data": []})
    assert df.height == 0


def test_parse_ship_detail() -> None:
    df = _parse_ship_detail(MOCK_SHIP_DETAIL_RESPONSE)
    assert df.height == 1
    assert "imo" in df.columns
    assert "vessel_name" in df.columns
    assert df[0, "imo"] == 9876543
    assert df[0, "vessel_name"] == "TEST CARRIER"


def test_parse_ship_detail_empty() -> None:
    df = _parse_ship_detail({})
    assert df.height == 0


@patch("src.collectors.shiplookup.write_raw")
@patch("src.collectors.shiplookup.search_ships")
def test_collect_ship_search(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_SEARCH_RESPONSE
    mock_write.return_value = 1

    count = collect_ship_search(name="carrier")
    assert count == 1
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()


@patch("src.collectors.shiplookup.write_raw")
@patch("src.collectors.shiplookup.get_ship_by_imo")
def test_collect_ship_by_imo(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_SHIP_DETAIL_RESPONSE
    mock_write.return_value = 1

    count = collect_ship_by_imo(9876543)
    assert count == 1
    mock_fetch.assert_called_once_with(9876543)
    mock_write.assert_called_once()
