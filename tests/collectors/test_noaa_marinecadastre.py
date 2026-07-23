from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.collectors.noaa_marinecadastre import (
    _parse_ais_parquet,
    collect_bulk_download,
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


def _create_test_parquet(path: Path, rows: int = 2) -> None:
    """Create a test parquet file with AIS-like columns."""
    data = {
        "MMSI": [123456789 + i for i in range(rows)],
        "IMO": [9876543 + i for i in range(rows)],
        "VesselName": [f"TEST VESSEL {i}" for i in range(rows)],
        "CallSign": [f"TEST{i}" for i in range(rows)],
        "VesselType": ["Container Ship"] * rows,
        "Status": ["Under way using engine"] * rows,
        "Length": [200.0] * rows,
        "Width": [30.0] * rows,
        "Draft": [10.0] * rows,
        "SOG": [12.5] * rows,
        "COG": [45.0] * rows,
        "Heading": [44.0] * rows,
        "BaseDateTime": ["2025-01-15T12:00:00"] * rows,
        "LAT": [40.7128] * rows,
        "LON": [-74.0060] * rows,
    }
    df = pl.DataFrame(data)
    path.parent.mkdir(parents=True, exist_ok=True)
    df.write_parquet(path)


def test_parse_ais_parquet() -> None:
    test_file = Path("test_ais.parquet")
    _create_test_parquet(test_file)

    try:
        df = _parse_ais_parquet(test_file)
        assert df.height == 2
        assert "mmsi" in df.columns
        assert "imo" in df.columns
        assert "vessel_name" in df.columns
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert "source" in df.columns
        assert df[0, "source"] == "noaa_marinecadastre"
    finally:
        test_file.unlink(missing_ok=True)


def test_parse_ais_parquet_empty() -> None:
    test_file = Path("test_empty.parquet")
    _create_test_parquet(test_file, rows=0)

    try:
        df = _parse_ais_parquet(test_file)
        assert df.height == 0
    finally:
        test_file.unlink(missing_ok=True)


@patch("src.collectors.noaa_marinecadastre.write_raw")
@patch("src.collectors.noaa_marinecadastre.download_file")
@patch("src.collectors.noaa_marinecadastre._parse_ais_parquet")
def test_collect_bulk_download(
    mock_parse: MagicMock,
    mock_download: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()

    mock_download.return_value = True
    mock_parse.return_value = pl.DataFrame({
        "mmsi": [123456789],
        "imo": [9876543],
        "vessel_name": ["TEST"],
        "latitude": [40.7128],
        "longitude": [-74.0060],
        "source": ["noaa_marinecadastre"],
        "partition_date": [date.today()],
    })
    mock_write.return_value = 1

    count = collect_bulk_download(2025, month=1)
    assert count == 1
    mock_download.assert_called_once()
    mock_write.assert_called_once()
