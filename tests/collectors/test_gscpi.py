from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.gscpi import _parse_nyfed_date, parse_gscpi_csv

SAMPLE_CSV = """Date,Jan-22,Feb-22,Mar-22
30-Sep-1997,#N/A,#N/A,0.11
31-Oct-1997,#N/A,0.09,0.12
30-Nov-1997,#N/A,#N/A,0.14
not-a-date,1.0,2.0,3.0
31-Dec-1997,#N/A,,
"""


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


def test_parse_nyfed_date() -> None:
    parsed = _parse_nyfed_date("30-Sep-1997")
    assert parsed is not None
    assert parsed.isoformat() == "1997-09-30"
    assert _parse_nyfed_date("31-Feb-1997") is None
    assert _parse_nyfed_date("garbage") is None


def test_parse_gscpi_csv() -> None:
    df = parse_gscpi_csv(SAMPLE_CSV)
    assert df.height == 3
    assert df[0, "index_date"].isoformat() == "1997-09-30"
    assert df[0, "gscpi_index"] == 0.11
    assert df[1, "gscpi_index"] == 0.12
    assert df[2, "gscpi_index"] == 0.14
    assert "source" in df.columns
    assert set(df["source"].unique().to_list()) == {"nyfed_gscpi"}


def test_parse_gscpi_csv_empty() -> None:
    assert parse_gscpi_csv(None).height == 0
    assert parse_gscpi_csv("").height == 0


@patch("src.collectors.gscpi.write_raw")
@patch("src.collectors.gscpi.fetch_gscpi_data")
def test_collect_gscpi(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = SAMPLE_CSV
    mock_write.return_value = 3

    from src.collectors.gscpi import collect_gscpi_data

    count = collect_gscpi_data()
    assert count == 3
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()
