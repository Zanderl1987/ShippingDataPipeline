from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.bts_air_cargo import _hidden_value, _to_float, _to_int, parse_t100_csv

T100_CSV = """MONTH,FREIGHT,PASSENGERS,UNIQUE_CARRIER,UNIQUE_CARRIER_NAME,ORIGIN,DEST
1,12345.5,100,5X,UPS,ANC,SDF
,0,0,,,ORD,LAX
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


def test_hidden_value() -> None:
    html = '<input type="hidden" id="__VIEWSTATE" value="abc123" />'
    assert _hidden_value(html, "__VIEWSTATE") == "abc123"
    assert _hidden_value(html, "missing") == ""


def test_to_int() -> None:
    assert _to_int("3") == 3
    assert _to_int("3.0") == 3
    assert _to_int("") is None
    assert _to_int("nan") is None
    assert _to_int(None) is None


def test_to_float() -> None:
    assert _to_float("12345.5") == 12345.5
    assert _to_float("") is None
    assert _to_float(None) is None


def test_parse_t100_csv() -> None:
    df = parse_t100_csv(T100_CSV, 2026)
    assert df.height == 1
    assert set(df["source"].unique().to_list()) == {"bts_t100"}
    assert df[0, "cargo_year"] == 2026
    assert df[0, "cargo_month"] == 1
    assert df[0, "carrier_code"] == "5X"
    assert df[0, "origin"] == "ANC"
    assert df[0, "dest"] == "SDF"
    assert df[0, "freight_pounds"] == 12345.5
    assert df[0, "passengers"] == 100.0


def test_parse_t100_csv_empty() -> None:
    assert parse_t100_csv("", 2026).height == 0
    assert parse_t100_csv(None, 2026).height == 0


@patch("src.collectors.bts_air_cargo.write_raw")
@patch("src.collectors.bts_air_cargo.download_year")
def test_collect_bts_air_cargo(
    mock_download: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_download.return_value = T100_CSV
    mock_write.return_value = 4

    from src.collectors.bts_air_cargo import collect_bts_air_cargo_data

    count = collect_bts_air_cargo_data(years=[2026])
    assert count == 4
    assert mock_download.call_count == 2
    mock_write.assert_called_once()
