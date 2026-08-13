from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.singapore_mpa import _to_float, parse_singapore_metrics

VESSEL_CALLS = "d_60410de1bc1e63ddcf51a619081b11b3"
REGISTERED = "d_56f64b2d5a31eb0ee465cc51e83ac60a"


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


def test_to_float() -> None:
    assert _to_float("1,234.5") == 1234.5
    assert _to_float(500) == 500.0
    assert _to_float(None) is None
    assert _to_float("n/a") is None


def test_parse_singapore_metrics() -> None:
    records = {
        VESSEL_CALLS: [
            {"year": "2024", "purpose_type": "Container", "number_of_vessel_calls": "1,234"},
            {"year": "2024", "purpose_type": "", "number_of_vessel_calls": "56"},
            {"year": "not-a-year", "purpose_type": "Bulk", "number_of_vessel_calls": "10"},
        ],
        REGISTERED: [
            {"month": "2024-01", "number_of_vessels": "3,456"},
        ],
    }
    df = parse_singapore_metrics(records)
    assert df.height == 3
    assert set(df["source"].unique().to_list()) == {"singapore_mpa"}
    assert set(df["metric_name"].unique().to_list()) == {"vessel_calls", "registered_vessels"}
    assert df[0, "metric_period"] == "2024"
    assert df[0, "metric_year"] == 2024
    assert df[0, "category"] == "Container"
    assert df[0, "value"] == 1234.0
    assert df[1, "category"] == "ALL"
    assert df[2, "metric_period"] == "2024-01"
    assert df[2, "value"] == 3456.0


def test_parse_singapore_metrics_empty() -> None:
    assert parse_singapore_metrics({}).height == 0


@patch("src.collectors.singapore_mpa.write_raw")
@patch("src.collectors.singapore_mpa.fetch_dataset")
def test_collect_singapore_mpa(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = [
        {"year": "2024", "purpose_type": "Container", "number_of_vessel_calls": "1,234"}
    ]
    mock_write.return_value = 1

    from src.collectors.singapore_mpa import collect_singapore_mpa_data

    count = collect_singapore_mpa_data()
    assert count == 1
    assert mock_fetch.call_count == 3
    mock_write.assert_called_once()
