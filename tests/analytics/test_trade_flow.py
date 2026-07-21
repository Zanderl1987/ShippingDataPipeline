from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.analytics.trade_flow import (
    analyze_port_pairs,
    get_vessel_destination_summary,
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
            "mmsi": [111, 111, 111, 222, 222],
            "imo": [1111111, 1111111, 1111111, 2222222, 2222222],
            "vessel_name": ["SHIP A", "SHIP A", "SHIP A", "SHIP B", "SHIP B"],
            "latitude": [51.0, 51.5, 52.0, 40.0, 40.5],
            "longitude": [1.0, 1.5, 2.0, -74.0, -73.5],
            "sog": [10.0, 12.0, 11.0, 8.0, 9.0],
            "cog": [90.0, 90.0, 90.0, 180.0, 180.0],
            "heading": [90.0, 90.0, 90.0, 180.0, 180.0],
            "nav_status": ["Under way"] * 5,
            "draught": [10.0, 10.0, 10.0, 12.0, 12.0],
            "destination": ["NLRTM", "DEHAM", "NLRTM", "USNYC", "USLAX"],
            "eta": ["2026-07-25"] * 5,
            "timestamp": [
                datetime(2026, 7, 21, 8, 0),
                datetime(2026, 7, 21, 10, 0),
                datetime(2026, 7, 21, 12, 0),
                datetime(2026, 7, 21, 9, 0),
                datetime(2026, 7, 21, 11, 0),
            ],
            "source": ["test"] * 5,
            "partition_date": [date(2026, 7, 21)] * 5,
        }
    )


@patch("src.analytics.trade_flow.read_dataset")
def test_analyze_port_pairs(
    mock_read: MagicMock,
    mock_settings: None,
    sample_positions: pl.DataFrame,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_read.return_value = sample_positions

    pairs = analyze_port_pairs()
    assert pairs.height > 0
    assert "origin_port" in pairs.columns
    assert "destination_port" in pairs.columns
    assert "vessel_count" in pairs.columns


@patch("src.analytics.trade_flow.read_dataset")
def test_get_vessel_destination_summary(
    mock_read: MagicMock,
    mock_settings: None,
    sample_positions: pl.DataFrame,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_read.return_value = sample_positions

    summary = get_vessel_destination_summary()
    assert summary.height > 0
    assert "unique_vessels" in summary.columns
    assert "destination" in summary.columns
