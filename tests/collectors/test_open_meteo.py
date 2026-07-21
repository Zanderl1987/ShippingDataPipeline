from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.open_meteo import (
    _parse_marine_response,
    _parse_weather_response,
    collect_marine,
    collect_weather,
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


MOCK_MARINE_RESPONSE = {
    "latitude": 54.54,
    "longitude": 10.23,
    "hourly": {
        "time": [
            "2026-07-21T00:00",
            "2026-07-21T01:00",
            "2026-07-21T02:00",
        ],
        "wave_height": [1.2, 1.5, 1.8],
        "wave_direction": [180.0, 190.0, 200.0],
        "wave_period": [6.0, 6.5, 7.0],
        "swell_wave_height": [0.8, 1.0, 1.2],
        "swell_wave_direction": [170.0, 175.0, 180.0],
        "swell_wave_period": [8.0, 8.5, 9.0],
        "ocean_current_velocity": [0.3, 0.4, 0.5],
        "ocean_current_direction": [90.0, 95.0, 100.0],
        "sea_surface_temperature": [18.5, 18.6, 18.7],
    },
}

MOCK_WEATHER_RESPONSE = {
    "latitude": 54.54,
    "longitude": 10.23,
    "hourly": {
        "time": [
            "2026-07-21T00:00",
            "2026-07-21T01:00",
            "2026-07-21T02:00",
        ],
        "wind_speed_10m": [15.0, 18.0, 20.0],
        "wind_direction_10m": [270.0, 275.0, 280.0],
        "wind_gusts_10m": [25.0, 30.0, 35.0],
        "pressure_msl": [1013.0, 1012.5, 1012.0],
        "temperature_2m": [15.0, 15.5, 16.0],
        "precipitation": [0.0, 0.5, 1.0],
    },
}


def test_parse_marine_response() -> None:
    df = _parse_marine_response(MOCK_MARINE_RESPONSE, 54.54, 10.23)
    assert df.height == 3
    assert "wave_height" in df.columns
    assert "wave_direction" in df.columns
    assert "swell_wave_height" in df.columns
    assert "ocean_current_velocity" in df.columns
    assert "sea_surface_temperature" in df.columns
    assert "latitude" in df.columns
    assert "longitude" in df.columns
    assert "source" in df.columns
    assert df[0, "wave_height"] == 1.2
    assert df[0, "source"] == "open_meteo"


def test_parse_marine_response_empty() -> None:
    df = _parse_marine_response({"hourly": {"time": []}}, 54.54, 10.23)
    assert df.height == 0


def test_parse_weather_response() -> None:
    df = _parse_weather_response(MOCK_WEATHER_RESPONSE, 54.54, 10.23)
    assert df.height == 3
    assert "wind_speed_10m" in df.columns
    assert "wind_direction_10m" in df.columns
    assert "wind_gusts_10m" in df.columns
    assert "pressure_msl" in df.columns
    assert "temperature_2m" in df.columns
    assert "precipitation" in df.columns
    assert "source" in df.columns
    assert df[0, "wind_speed_10m"] == 15.0
    assert df[0, "source"] == "open_meteo"


def test_parse_weather_response_empty() -> None:
    df = _parse_weather_response({"hourly": {"time": []}}, 54.54, 10.23)
    assert df.height == 0


@patch("src.collectors.open_meteo.write_raw")
@patch("src.collectors.open_meteo.fetch_marine")
def test_collect_marine(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_MARINE_RESPONSE
    mock_write.return_value = 3

    count = collect_marine(54.54, 10.23)
    assert count == 3
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()


@patch("src.collectors.open_meteo.write_raw")
@patch("src.collectors.open_meteo.fetch_weather")
def test_collect_weather(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_WEATHER_RESPONSE
    mock_write.return_value = 3

    count = collect_weather(54.54, 10.23)
    assert count == 3
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()
