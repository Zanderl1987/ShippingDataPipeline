from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.barentswatch import (
    _parse_positions,
    _parse_track,
    collect_latest_positions,
    collect_vessel_track,
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


MOCK_POSITIONS_RESPONSE = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [5.32, 60.39]},
            "properties": {
                "mmsi": 257111020,
                "imo": 9876543,
                "shipname": "NORWEGIAN CARRIER",
                "shiptype": "Container Ship",
                "sog": 12.5,
                "cog": 45.0,
                "heading": 44,
                "navstatus": "Under way using engine",
                "draught": 10.5,
                "destination": "NOBGO",
                "eta": "2026-04-28T06:00:00Z",
                "timestamp": "2026-04-27T18:30:00Z",
                "flag": "NO",
                "stream": "terra",
            },
        }
    ],
}

MOCK_TRACK_RESPONSE = {
    "positions": [
        {
            "mmsi": 257111020,
            "lat": 60.39,
            "lon": 5.32,
            "sog": 12.5,
            "cog": 45.0,
            "heading": 44,
            "navstatus": "Under way using engine",
            "timestamp": "2026-04-27T18:30:00Z",
        },
        {
            "mmsi": 257111020,
            "lat": 60.40,
            "lon": 5.33,
            "sog": 12.3,
            "cog": 46.0,
            "heading": 45,
            "navstatus": "Under way using engine",
            "timestamp": "2026-04-27T17:30:00Z",
        },
    ]
}


def test_parse_positions() -> None:
    df = _parse_positions(MOCK_POSITIONS_RESPONSE)
    assert df.height == 1
    assert "mmsi" in df.columns
    assert "imo" in df.columns
    assert "vessel_name" in df.columns
    assert "source" in df.columns
    assert df[0, "vessel_name"] == "NORWEGIAN CARRIER"
    assert df[0, "mmsi"] == 257111020
    assert df[0, "source"] == "barentswatch"


def test_parse_positions_empty() -> None:
    df = _parse_positions({})
    assert df.height == 0


def test_parse_track() -> None:
    df = _parse_track(MOCK_TRACK_RESPONSE)
    assert df.height == 2
    assert "mmsi" in df.columns
    assert "latitude" in df.columns
    assert "longitude" in df.columns
    assert "source" in df.columns
    assert df[0, "mmsi"] == 257111020
    assert df[0, "source"] == "barentswatch"


def test_parse_track_empty() -> None:
    df = _parse_track({})
    assert df.height == 0


@patch("src.collectors.barentswatch.write_raw")
@patch("src.collectors.barentswatch.get_latest_positions")
def test_collect_latest_positions(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_POSITIONS_RESPONSE
    mock_write.return_value = 1

    count = collect_latest_positions()
    assert count == 1
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()


@patch("src.collectors.barentswatch.write_raw")
@patch("src.collectors.barentswatch.get_vessel_track")
def test_collect_vessel_track(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_TRACK_RESPONSE
    mock_write.return_value = 2

    count = collect_vessel_track(257111020, hours=4)
    assert count == 2
    mock_fetch.assert_called_once_with(257111020, 4)
    mock_write.assert_called_once()
