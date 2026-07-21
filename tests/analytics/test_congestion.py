from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.analytics.congestion import (
    calculate_port_activity,
    estimate_congestion,
    get_vessel_dwell_times,
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


@pytest.fixture
def sample_positions() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "mmsi": [111, 111, 222, 222, 333],
            "imo": [1111111, 1111111, 2222222, 2222222, 3333333],
            "vessel_name": ["SHIP A", "SHIP A", "SHIP B", "SHIP B", "SHIP C"],
            "latitude": [51.0, 51.0, 40.0, 40.0, 35.0],
            "longitude": [1.0, 1.0, -74.0, -74.0, 140.0],
            "sog": [0.5, 0.8, 12.0, 13.0, 0.3],
            "cog": [0.0, 0.0, 180.0, 180.0, 90.0],
            "heading": [0.0, 0.0, 180.0, 180.0, 90.0],
            "nav_status": ["At anchor", "At anchor", "Under way", "Under way", "Moored"],
            "draught": [10.0, 10.0, 12.0, 12.0, 8.0],
            "destination": ["NLRTM", "NLRTM", "USNYC", "USNYC", "JPTYO"],
            "eta": ["2026-07-25"] * 5,
            "timestamp": [
                datetime(2026, 7, 21, 8, 0),
                datetime(2026, 7, 21, 10, 0),
                datetime(2026, 7, 21, 9, 0),
                datetime(2026, 7, 21, 11, 0),
                datetime(2026, 7, 21, 8, 0),
            ],
            "source": ["test"] * 5,
            "partition_date": [date(2026, 7, 21)] * 5,
        }
    )


@patch("src.analytics.congestion.read_dataset")
def test_calculate_port_activity(
    mock_read: MagicMock,
    mock_settings: None,
    sample_positions: pl.DataFrame,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_read.return_value = sample_positions

    activity = calculate_port_activity()
    assert activity.height > 0
    assert "unique_vessels" in activity.columns


@patch("src.analytics.congestion.read_dataset")
def test_estimate_congestion(
    mock_read: MagicMock,
    mock_settings: None,
    sample_positions: pl.DataFrame,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_read.return_value = sample_positions

    congestion = estimate_congestion()
    assert congestion.height > 0
    assert "congestion_ratio" in congestion.columns


@patch("src.analytics.congestion.read_dataset")
def test_get_vessel_dwell_times(
    mock_read: MagicMock,
    mock_settings: None,
    sample_positions: pl.DataFrame,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_read.return_value = sample_positions

    dwell = get_vessel_dwell_times()
    assert dwell.height > 0
    assert "dwell_seconds" in dwell.columns
