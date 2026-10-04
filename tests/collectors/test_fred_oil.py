from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.fred_oil import parse_fred_csv

# The shape fredgraph.csv returns: "" for a day without a price.
CSV = """observation_date,DCOILBRENTEU,DCOILWTICO
2026-01-02,76.20,72.50
2026-01-05,76.80,73.10
2026-01-06,,
2026-01-07,,71.90
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


def test_parse_fred_csv_merges_series() -> None:
    df = parse_fred_csv(CSV)
    # 2026-01-06 has no price at all, so it's dropped rather than kept as nulls.
    assert df.height == 3
    by_date = {r["price_date"]: r for r in df.to_dicts()}
    assert by_date[date(2026, 1, 2)]["wti_usd"] == pytest.approx(72.50)
    assert by_date[date(2026, 1, 2)]["brent_usd"] == pytest.approx(76.20)
    assert by_date[date(2026, 1, 7)]["brent_usd"] is None
    assert set(df["source"].unique().to_list()) == {"fred"}


def test_parse_fred_csv_old_header_and_missing_marker() -> None:
    df = parse_fred_csv("DATE,DCOILWTICO\n2026-01-02,72.5\n2026-01-05,.\n")
    assert df.height == 1 and df.columns[:2] == ["price_date", "wti_usd"]


def test_parse_fred_csv_unknown_series_or_empty() -> None:
    assert parse_fred_csv("observation_date,OTHER\n2026-01-02,1\n").height == 0
    assert parse_fred_csv("").height == 0


def _collect(mock_fetch: MagicMock, *, bulk: bool) -> None:
    from src.collectors.fred_oil import collect_oil_prices
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = CSV
    collect_oil_prices(bulk_backfill=bulk)


@patch("src.collectors.fred_oil.write_raw", return_value=3)
@patch("src.collectors.fred_oil.fetch_prices_csv")
def test_ci_fetches_full_history_even_when_table_has_rows(
    mock_fetch: MagicMock, mock_write: MagicMock, mock_settings: None,
) -> None:
    from src.collectors.fred_oil import parse_fred_csv
    from src.storage.writer import init_db, write_raw

    init_db()
    write_raw("fred", parse_fred_csv(CSV), table_name="oil_prices")  # table already populated
    _collect(mock_fetch, bulk=True)
    assert mock_fetch.call_args.args[0] is None  # no start date: everything
    assert mock_write.call_args.args[1].height == 3


@patch("src.collectors.fred_oil.get_with_retry")
def test_fetch_sends_date_range_for_every_series(mock_get: MagicMock) -> None:
    # fredgraph.csv takes one cosd/coed value per series; a single value
    # limits only the first series and returns the others' full history.
    from src.collectors.fred_oil import SERIES_TO_COLUMN, fetch_prices_csv

    mock_get.return_value.text = CSV
    fetch_prices_csv("2026-01-01", "2026-01-31")
    params = mock_get.call_args.kwargs["params"]
    n = len(SERIES_TO_COLUMN)
    assert params["cosd"] == ",".join(["2026-01-01"] * n)
    assert params["coed"] == ",".join(["2026-01-31"] * n)


@patch("src.collectors.fred_oil.write_raw", return_value=1)
@patch("src.collectors.fred_oil.fetch_prices_csv", return_value=CSV)
def test_rows_before_start_date_are_not_written(
    mock_fetch: MagicMock, mock_write: MagicMock, mock_settings: None,
) -> None:
    # Rows outside the requested window would overwrite stored history with
    # whatever columns this response happens to carry.
    from src.collectors.fred_oil import collect_oil_prices
    from src.storage.writer import init_db

    init_db()
    collect_oil_prices(start_date="2026-01-06", bulk_backfill=False)
    written = mock_write.call_args.args[1]
    assert written["price_date"].to_list() == [date(2026, 1, 7)]


@patch("src.collectors.fred_oil.write_raw", return_value=3)
@patch("src.collectors.fred_oil.fetch_prices_csv")
def test_local_run_fetches_recent_days_only(
    mock_fetch: MagicMock, mock_write: MagicMock, mock_settings: None,
) -> None:
    _collect(mock_fetch, bulk=False)
    start = date.fromisoformat(mock_fetch.call_args.args[0])
    assert (date.today() - start).days == 30


@patch("src.collectors.fred_oil.write_raw")
@patch("src.collectors.fred_oil.fetch_prices_csv", return_value="")
def test_collect_oil_prices_empty(
    mock_fetch: MagicMock, mock_write: MagicMock, mock_settings: None,
) -> None:
    from src.collectors.fred_oil import collect_oil_prices
    from src.storage.writer import init_db

    init_db()
    assert collect_oil_prices(bulk_backfill=False) == 0
    mock_write.assert_not_called()
