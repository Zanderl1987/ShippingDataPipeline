from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.un_comtrade import (
    _parse_trade_data,
    collect_trade_data,
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


MOCK_TRADE_RESPONSE = {
    "data": [
        {
            "rtCode": 156,
            "ptCode": 842,
            "cmdCode": "TOTAL",
            "flowCode": "X",
            "period": 2025,
            "primaryValue": 500000000000,
            "netWgt": 250000000000,
            "grossWgt": 260000000000,
        },
        {
            "rtCode": 156,
            "ptCode": 276,
            "cmdCode": "TOTAL",
            "flowCode": "X",
            "period": 2025,
            "primaryValue": 120000000000,
            "netWgt": 80000000000,
            "grossWgt": 85000000000,
        },
    ],
    "count": 2,
}


def test_parse_trade_data() -> None:
    df = _parse_trade_data(MOCK_TRADE_RESPONSE)
    assert df.height == 2
    assert "reporter_code" in df.columns
    assert "partner_code" in df.columns
    assert "trade_value_usd" in df.columns
    assert "net_weight_kg" in df.columns
    assert df[0, "reporter_code"] == 156
    assert df[0, "partner_code"] == 842
    assert df[0, "flow_code"] == "X"


def test_parse_trade_data_empty() -> None:
    df = _parse_trade_data({"data": []})
    assert df.height == 0


@patch("src.collectors.un_comtrade.write_raw")
@patch("src.collectors.un_comtrade.get_trade_data")
def test_collect_trade_data(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_TRADE_RESPONSE
    mock_write.return_value = 2

    count = collect_trade_data(reporter_code=156, flow_code="X", period="2025")
    assert count == 2
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()
