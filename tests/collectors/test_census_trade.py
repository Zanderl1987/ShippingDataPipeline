from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.census_trade import parse_census_trade_data

EXPORTS_ROWS = [
    ["E_COMMODITY", "CTY_CODE", "CTY_NAME", "ALL_VAL_MO", "COMM_LVL", "time"],
    ["27", "1220", "GERMANY", "1500000", "HS2", "2026-01"],
    ["84", "1220", "GERMANY", "2500000", "HS2", "2026-01"],
    ["27", "5700", "CHINA", "not_a_number", "HS2", "2026-01"],
]

IMPORTS_ROWS = [
    ["I_COMMODITY", "CTY_CODE", "CTY_NAME", "GEN_VAL_MO", "COMM_LVL", "time"],
    ["85", "5700", "CHINA", "9000000", "HS2", "2026-01"],
]


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


def test_parse_census_trade_data_exports() -> None:
    df = parse_census_trade_data(EXPORTS_ROWS, "X", "2026-01")
    # The malformed value row is dropped, not crashed on.
    assert df.height == 2
    by_partner = {r["partner_code"]: r for r in df.to_dicts()}
    assert by_partner["1220"]["trade_value_usd"] in {1500000.0, 2500000.0}
    assert set(df["reporter_code"].unique().to_list()) == {"US"}
    assert set(df["flow_code"].unique().to_list()) == {"X"}
    assert set(df["currency"].unique().to_list()) == {"USD"}
    assert set(df["source"].unique().to_list()) == {"census_trade"}
    assert set(df["year"].unique().to_list()) == {2026}


def test_parse_census_trade_data_imports() -> None:
    df = parse_census_trade_data(IMPORTS_ROWS, "M", "2026-01")
    assert df.height == 1
    row = df.to_dicts()[0]
    assert row["partner_code"] == "5700"
    assert row["commodity_code"] == "85"
    assert row["trade_value_usd"] == pytest.approx(9000000.0)
    assert row["flow_code"] == "M"


def test_parse_census_trade_data_empty() -> None:
    assert parse_census_trade_data([], "X", "2026-01").height == 0
    assert parse_census_trade_data([EXPORTS_ROWS[0]], "X", "2026-01").height == 0


def test_parse_census_trade_data_missing_columns() -> None:
    bad_rows = [["SOME_OTHER_FIELD"], ["value"]]
    assert parse_census_trade_data(bad_rows, "X", "2026-01").height == 0


def test_fetch_trade_data_invalid_flow_code() -> None:
    from src.collectors.census_trade import fetch_trade_data

    with pytest.raises(ValueError, match="flow_code"):
        fetch_trade_data(flow_code="Z", period="2026-01")


def test_fetch_trade_data_requires_key() -> None:
    from src.collectors.census_trade import fetch_trade_data
    from src.config import settings

    old_key = settings.census_api_key
    settings.census_api_key = None
    try:
        with pytest.raises(ValueError, match="CENSUS_API_KEY"):
            fetch_trade_data(flow_code="X", period="2026-01")
    finally:
        settings.census_api_key = old_key


@patch("src.collectors.census_trade.write_raw")
@patch("src.collectors.census_trade.fetch_trade_data")
def test_collect_trade_data(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.side_effect = [EXPORTS_ROWS, IMPORTS_ROWS]
    mock_write.return_value = 3

    from src.collectors.census_trade import collect_trade_data

    count = collect_trade_data(period="2026-01")
    assert count == 3
    assert mock_fetch.call_count == 2
    mock_write.assert_called_once()
    written_df = mock_write.call_args[0][1]
    assert written_df.height == 3


@patch("src.collectors.census_trade.write_raw")
@patch("src.collectors.census_trade.fetch_trade_data")
def test_collect_trade_data_empty(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = []

    from src.collectors.census_trade import collect_trade_data

    count = collect_trade_data(period="2026-01")
    assert count == 0
    mock_write.assert_not_called()


@patch("src.collectors.census_trade.write_raw")
@patch("src.collectors.census_trade.fetch_trade_data")
def test_collect_trade_data_default_period(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    """No explicit period defaults to 2 months before today, not a crash."""
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = []

    from src.collectors.census_trade import collect_trade_data

    collect_trade_data()
    assert mock_fetch.call_count == 2
    called_period = mock_fetch.call_args_list[0].kwargs["period"]
    assert len(called_period) == 7 and called_period[4] == "-"
