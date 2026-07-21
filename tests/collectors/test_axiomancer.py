from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.axiomancer import (
    _parse_global_snapshot,
    _parse_port_positions,
    collect_global_snapshot,
    collect_port,
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


MOCK_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-46.30, -23.97]},
            "properties": {
                "imo": "9876543",
                "name": "TEST CARRIER",
                "vessel_type": "bulk_carrier",
                "flag": "PA",
                "speed": 12.4,
                "course": 87,
                "draft": 11.2,
                "destination": "BR SSZ",
                "nav_status": "under way using engine",
                "timestamp": "2026-04-27T18:42:11Z",
                "dwt": 81600,
                "length": 229,
                "beam": 32,
                "build_year": 2014,
            },
        },
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [4.0, 51.9]},
            "properties": {
                "imo": "1234567",
                "name": "TEST TANKER",
                "vessel_type": "tanker",
                "flag": "LR",
                "speed": 0.0,
                "course": 0,
                "draft": 14.5,
                "destination": "NLRTM",
                "nav_status": "at anchor",
                "timestamp": "2026-04-27T18:30:00Z",
            },
        },
    ],
    "meta": {
        "total_vessel_count": 2,
        "updated_at": "2026-04-27T18:42:30Z",
    },
}

MOCK_PORT_POSITIONS = [
    {
        "imo_number": "9876543",
        "name": "TEST CARRIER",
        "vessel_type": "bulk_carrier",
        "latitude": -23.97,
        "longitude": -46.30,
        "speed": 12.4,
        "course": 87,
        "draft": 11.2,
        "nav_status": "under way using engine",
        "destination": "BR SSZ",
    },
]

MOCK_VESSELS_RESPONSE = {
    "vessels": [
        {
            "imo": "9876543",
            "name": "TEST CARRIER",
            "type": "bulk_carrier",
            "flag": "PA",
            "dwt": 81600,
            "length": 229,
            "beam": 32,
        },
    ]
}


def test_parse_global_snapshot() -> None:
    df = _parse_global_snapshot(MOCK_GEOJSON)
    assert df.height == 2
    assert "imo" in df.columns
    assert "vessel_name" in df.columns
    assert "latitude" in df.columns
    assert "longitude" in df.columns
    assert "source" in df.columns
    assert df[0, "vessel_name"] == "TEST CARRIER"
    assert df[0, "imo"] == 9876543
    assert df[0, "source"] == "axiomancer"


def test_parse_global_snapshot_empty() -> None:
    df = _parse_global_snapshot({"type": "FeatureCollection", "features": []})
    assert df.height == 0


def test_parse_port_positions() -> None:
    df = _parse_port_positions(MOCK_PORT_POSITIONS)
    assert df.height == 1
    assert "imo" in df.columns
    assert "vessel_name" in df.columns
    assert df[0, "imo"] == 9876543
    assert df[0, "source"] == "axiomancer"


def test_parse_port_positions_empty() -> None:
    df = _parse_port_positions([])
    assert df.height == 0


@patch("src.collectors.axiomancer.write_raw")
@patch("src.collectors.axiomancer.fetch_positions_latest")
def test_collect_global_snapshot(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_GEOJSON
    mock_write.return_value = 2

    count = collect_global_snapshot()
    assert count == 2
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()


@patch("src.collectors.axiomancer.write_raw")
@patch("src.collectors.axiomancer.fetch_port_positions")
def test_collect_port(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_PORT_POSITIONS
    mock_write.return_value = 1

    count = collect_port("santos")
    assert count == 1
    mock_fetch.assert_called_once_with("santos")
    mock_write.assert_called_once()
