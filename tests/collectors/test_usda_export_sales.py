from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.usda_export_sales import parse_export_sales

ROWS = [
    {"date": "2026-09-17T00:00:00.000", "myear": "2026/2027", "country": "MEXICO",
     "commodity": "Corn", "unit": "Metric Tons", "wkexportscmy": "507744",
     "accexportscmy": "1301093", "outsalescmy": "5560016", "grosalescmy": "222425",
     "netsalescmy": "210981", "totcommcmy": "6861109", "outsalesnmy": "353000",
     "netsalesnmy": "0"},
    {"date": "2026-09-17T00:00:00.000", "myear": "2026/2027", "country": "JAPAN",
     "commodity": "Wheat", "type": "HRW", "unit": "Metric Tons", "wkexportscmy": "0",
     "netsalescmy": "-49970"},
    {"date": None, "commodity": "Corn"},
]


@pytest.fixture
def mock_settings(tmp_path: Path) -> None:
    from src.config import settings

    old_data, old_storage = settings.data_dir, settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    yield
    settings.data_dir, settings.storage_dir = old_data, old_storage


def test_parse_export_sales() -> None:
    df = parse_export_sales(ROWS)
    assert df.height == 2  # the row without a date is dropped
    corn = df.row(0, named=True)
    assert corn["week_ending"] == date(2026, 9, 17)
    assert corn["outstanding_sales"] == pytest.approx(5_560_016)
    assert corn["next_my_outstanding_sales"] == pytest.approx(353_000)
    assert corn["wheat_class"] is None
    assert corn["source"] == "usda_export_sales"
    wheat = df.row(1, named=True)
    assert wheat["wheat_class"] == "HRW"
    assert wheat["net_sales"] == pytest.approx(-49_970)  # cancellations are negative
    assert wheat["outstanding_sales"] is None  # missing field -> NULL


def test_parse_export_sales_empty() -> None:
    assert parse_export_sales([]).height == 0


@patch("src.collectors.usda_export_sales.requests.get")
def test_fetch_paginates_until_short_page(mock_get: MagicMock) -> None:
    from src.collectors import usda_export_sales as mod

    full = MagicMock(status_code=200)
    full.json.return_value = [{}] * mod.PAGE_SIZE
    short = MagicMock(status_code=200)
    short.json.return_value = [{}] * 3
    mock_get.side_effect = [full, short]
    rows = mod.fetch_export_sales(date(2026, 8, 1))
    assert len(rows) == mod.PAGE_SIZE + 3
    offsets = [c.kwargs["params"]["$offset"] for c in mock_get.call_args_list]
    assert offsets == [0, mod.PAGE_SIZE]
    assert "2026-08-01" in mock_get.call_args_list[0].kwargs["params"]["$where"]


@patch("src.collectors.usda_export_sales.write_raw")
@patch("src.collectors.usda_export_sales.fetch_export_sales")
def test_collect_uses_recent_window_without_bulk(
    mock_fetch: MagicMock, mock_write: MagicMock, mock_settings: None,
) -> None:
    from src.collectors.usda_export_sales import collect_export_sales
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = ROWS
    mock_write.return_value = 2
    assert collect_export_sales(bulk_backfill=False) == 2
    since = mock_fetch.call_args.args[0]
    assert since is not None and (date.today() - since).days == 56
    assert mock_write.call_args.kwargs["table_name"] == "us_export_sales"


@patch("src.collectors.usda_export_sales.write_raw")
@patch("src.collectors.usda_export_sales.fetch_export_sales")
def test_collect_full_history_when_bulk_and_empty(
    mock_fetch: MagicMock, mock_write: MagicMock, mock_settings: None,
) -> None:
    from src.collectors.usda_export_sales import collect_export_sales
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = ROWS
    mock_write.return_value = 2
    collect_export_sales(bulk_backfill=True)
    assert mock_fetch.call_args.args[0] is None
