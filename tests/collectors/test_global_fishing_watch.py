from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.global_fishing_watch import (
    _parse_events,
    _parse_vessel_search,
    collect_events,
    collect_vessel_search,
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


MOCK_VESSEL_SEARCH = {
    "entries": [
        {
            "id": "vessel-123",
            "registryInfo": [
                {
                    "imo": 9876543,
                    "mmsi": 123456789,
                    "name": "TEST FISHING VESSEL",
                    "shipType": "Fishing",
                    "flag": "PA",
                    "length": 45,
                    "beam": 10,
                    "grossTonnage": 499,
                    "dwt": 300,
                    "buildYear": 2010,
                }
            ],
        }
    ],
    "total": 1,
    "limit": 10,
    "offset": 0,
}

MOCK_EVENTS = {
    "entries": [
        {
            "id": "event-456",
            "type": "port_visit",
            "vessel": {
                "id": "vessel-123",
                "imo": 9876543,
                "mmsi": 123456789,
                "name": "TEST FISHING VESSEL",
            },
            "position": {"lat": 1.29, "lon": 103.85},
            "port": {
                "unlocode": "SGSIN",
                "name": "Singapore",
            },
            "start": "2026-04-27T10:00:00Z",
            "end": "2026-04-27T18:00:00Z",
        }
    ],
    "total": 1,
    "limit": 100,
    "offset": 0,
}


def test_parse_vessel_search() -> None:
    df = _parse_vessel_search(MOCK_VESSEL_SEARCH)
    assert df.height == 1
    assert "gfw_vessel_id" in df.columns
    assert "imo" in df.columns
    assert "vessel_name" in df.columns
    assert df[0, "vessel_name"] == "TEST FISHING VESSEL"
    assert df[0, "imo"] == 9876543
    assert df[0, "flag"] == "PA"


def test_parse_vessel_search_empty() -> None:
    df = _parse_vessel_search({"entries": []})
    assert df.height == 0


def test_parse_events() -> None:
    df = _parse_events(MOCK_EVENTS)
    assert df.height == 1
    assert "gfw_event_id" in df.columns
    assert "event_type" in df.columns
    assert "port_unlocode" in df.columns
    assert "source" in df.columns
    assert df[0, "event_type"] == "port_visit"
    assert df[0, "port_unlocode"] == "SGSIN"
    assert df[0, "source"] == "global_fishing_watch"


def test_parse_events_empty() -> None:
    df = _parse_events({"entries": []})
    assert df.height == 0


@patch("src.collectors.global_fishing_watch.write_raw")
@patch("src.collectors.global_fishing_watch.search_vessels")
def test_collect_vessel_search(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_VESSEL_SEARCH
    mock_write.return_value = 1

    count = collect_vessel_search("fishing vessel")
    assert count == 1
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()


@patch("src.collectors.global_fishing_watch.write_raw")
@patch("src.collectors.global_fishing_watch.get_port_visits")
def test_collect_events(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_EVENTS
    mock_write.return_value = 1

    count = collect_events("2026-04-01", "2026-04-30")
    assert count == 1
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()
