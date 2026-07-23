from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.vesselapi import (
    _parse_port_events,
    _parse_vessel,
    collect_port_events,
    collect_vessel,
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


MOCK_PORT_EVENTS_RESPONSE = {
    "portEvents": [
        {
            "type": "arrival",
            "time": "2026-04-27T14:30:00Z",
            "eta": "2026-04-27T14:00:00Z",
            "etd": None,
            "previousPort": "CNSHA",
            "nextPort": None,
            "vessel": {
                "imo": 9876543,
                "mmsi": 123456789,
                "name": "TEST CONTAINER SHIP",
            },
            "port": {
                "unlocode": "SGSIN",
                "name": "Singapore",
                "country": "SG",
            },
        }
    ]
}

MOCK_VESSEL_RESPONSE = {
    "imo": 9876543,
    "mmsi": 123456789,
    "name": "TEST CONTAINER SHIP",
    "type": "Container Ship",
    "flag": "PA",
    "callsign": "H3RC",
    "length": 366,
    "beam": 51,
    "grossTonnage": 150000,
    "deadweight": 160000,
    "yearBuilt": 2018,
    "owner": "Test Shipping Co",
    "manager": "Test Ship Management",
}


def test_parse_port_events() -> None:
    df = _parse_port_events(MOCK_PORT_EVENTS_RESPONSE)
    assert df.height == 1
    assert "imo" in df.columns
    assert "port_unlocode" in df.columns
    assert "event_type" in df.columns
    assert "source" in df.columns
    assert df[0, "event_type"] == "arrival"
    assert df[0, "port_unlocode"] == "SGSIN"
    assert df[0, "source"] == "vesselapi"


def test_parse_port_events_empty() -> None:
    df = _parse_port_events({"portEvents": []})
    assert df.height == 0


def test_parse_vessel() -> None:
    df = _parse_vessel(MOCK_VESSEL_RESPONSE)
    assert df.height == 1
    assert "imo" in df.columns
    assert "vessel_name" in df.columns
    assert "vessel_type" in df.columns
    assert df[0, "vessel_name"] == "TEST CONTAINER SHIP"
    assert df[0, "imo"] == 9876543
    assert df[0, "flag"] == "PA"


def test_parse_vessel_empty() -> None:
    df = _parse_vessel({})
    assert df.height == 0


@patch("src.collectors.vesselapi.write_raw")
@patch("src.collectors.vesselapi.get_port_events")
def test_collect_port_events(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_PORT_EVENTS_RESPONSE
    mock_write.return_value = 1

    count = collect_port_events(country="SG")
    assert count == 1
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()


@patch("src.collectors.vesselapi.write_raw")
@patch("src.collectors.vesselapi.get_vessel")
def test_collect_vessel(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_VESSEL_RESPONSE
    mock_write.return_value = 1

    count = collect_vessel("9876543")
    assert count == 1
    mock_fetch.assert_called_once_with("9876543")
    mock_write.assert_called_once()
