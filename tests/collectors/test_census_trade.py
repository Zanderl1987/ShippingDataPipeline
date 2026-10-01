from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.census_trade import (
    _EXPORT_PARTNER_VARS,
    _IMPORT_PRODUCT_VARS,
    all_periods,
    parse_census_rows,
    recent_periods,
)

# Census echoes predicate fields again at the end of each row.
IMPORT_PRODUCT_ROWS = [
    list(_IMPORT_PRODUCT_VARS) + ["time", "COMM_LVL", "CTY_CODE"],
    ["8517130000", "SMARTPHONES", "5000000", "4900000", "1000", "0", "990", "0",
     "NO", "-", "4900000", "122500", "5100000", "100000", "1000000", "2000",
     "4000000", "500", "900000", "1800", "2026-07", "HS10", "-"],
    ["8517620000", "MACHINES", "bad", "", "", "", "", "", "-", "-", "", "", "",
     "", "", "", "", "", "", "", "2026-07", "HS10", "-"],
]

EXPORT_PARTNER_ROWS = [
    list(_EXPORT_PARTNER_VARS) + ["time", "COMM_LVL"],
    ["10", "CEREALS", "-", "TOTAL FOR ALL COUNTRIES", "900", "800", "1", "0", "0", "0", "0",
     "2026-07", "HS2"],
    ["10", "CEREALS", "0020", "USMCA (NAFTA)", "300", "0", "0", "0", "0", "0", "0",
     "2026-07", "HS2"],
    ["10", "CEREALS", "5XXX", "ASIA", "450", "0", "0", "0", "0", "0", "0",
     "2026-07", "HS2"],
    ["10", "CEREALS", "2010", "MEXICO", "250", "0", "0", "0", "0", "0", "0",
     "2026-07", "HS2"],
    ["10", "CEREALS", "5880", "JAPAN", "200", "200", "1", "0", "0", "0", "0",
     "2026-07", "HS2"],
]


@pytest.fixture
def mock_settings(tmp_path: Path) -> None:
    from src.config import settings

    old_data, old_storage, old_key = (
        settings.data_dir, settings.storage_dir, settings.census_api_key,
    )
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.census_api_key = "test-key"
    settings.ensure_dirs()
    yield
    settings.data_dir, settings.storage_dir, settings.census_api_key = (
        old_data, old_storage, old_key,
    )


def test_parse_products_types_and_period() -> None:
    df = parse_census_rows(IMPORT_PRODUCT_ROWS, _IMPORT_PRODUCT_VARS)
    assert df.height == 2
    phones = df.row(0, named=True)
    assert phones["commodity_code"] == "8517130000"
    assert phones["value_usd"] == pytest.approx(5_000_000)
    assert phones["calculated_duty_usd"] == pytest.approx(122_500)
    assert phones["unit_1"] == "NO"
    assert phones["unit_2"] is None  # "-" means no second unit
    assert phones["period_date"] == date(2026, 7, 1)
    assert (phones["year"], phones["month"]) == (2026, 7)
    assert phones["source"] == "census_trade"
    # Unparseable numbers become NULL instead of dropping the product.
    assert df.row(1, named=True)["value_usd"] is None


def test_parse_empty_and_missing_columns() -> None:
    assert parse_census_rows([], _IMPORT_PRODUCT_VARS).height == 0
    assert parse_census_rows([["OTHER"], ["x"]], _IMPORT_PRODUCT_VARS).height == 0


@patch("src.collectors.census_trade.fetch_census")
def test_fetch_partners_drops_world_total_and_groups(
    mock_fetch: MagicMock, mock_settings: None,
) -> None:
    from src.collectors.census_trade import fetch_partners

    mock_fetch.side_effect = [[], EXPORT_PARTNER_ROWS]  # imports empty, exports
    df = fetch_partners("2026-07", "2026-07", "10")
    assert sorted(df["partner_code"].to_list()) == ["2010", "5880"]
    assert set(df["flow_code"].to_list()) == {"X"}


@patch("src.collectors.census_trade.fetch_census")
def test_fetch_products_splits_export_origin(
    mock_fetch: MagicMock, mock_settings: None,
) -> None:
    from src.collectors.census_trade import _EXPORT_PRODUCT_VARS, fetch_products

    export_rows = [
        list(_EXPORT_PRODUCT_VARS) + ["time"],
        ["1005900000", "CORN", "100", "10", "0", "T", "-", "100", "50", "0", "0", "0", "0",
         "2026-07"],
    ]
    mock_fetch.side_effect = [IMPORT_PRODUCT_ROWS, export_rows, export_rows]
    df = fetch_products("2026-07", "2026-07")
    assert df.height == 4
    assert sorted(set(zip(df["flow_code"], df["export_origin"]))) == [
        ("M", "all"), ("X", "domestic"), ("X", "foreign"),
    ]
    dfs = [c.args[1].get("DF") for c in mock_fetch.call_args_list]
    assert dfs == [None, "1", "2"]


def test_fetch_census_requires_key() -> None:
    from src.collectors.census_trade import fetch_census
    from src.config import settings

    old_key = settings.census_api_key
    settings.census_api_key = None
    try:
        with pytest.raises(ValueError, match="CENSUS_API_KEY"):
            fetch_census("imports/hs", {})
    finally:
        settings.census_api_key = old_key


@patch("src.collectors.census_trade.requests.get")
def test_fetch_census_rejected_key_redirect(mock_get: MagicMock, mock_settings: None) -> None:
    from src.collectors.census_trade import fetch_census

    mock_get.return_value = MagicMock(
        status_code=302, headers={"Location": "https://api.census.gov/data/invalid_key.html"},
    )
    with pytest.raises(ValueError, match="rejected the API key"):
        fetch_census("imports/hs", {})


@patch("src.collectors.census_trade.requests.get")
def test_fetch_census_no_content_is_empty(mock_get: MagicMock, mock_settings: None) -> None:
    from src.collectors.census_trade import fetch_census

    mock_get.return_value = MagicMock(status_code=204)
    assert fetch_census("imports/hs", {}) == []


def test_periods_recent_window() -> None:
    assert recent_periods(date(2026, 9, 30)) == ["2026-05", "2026-06", "2026-07", "2026-08"]
    # January wraps into the previous year.
    assert recent_periods(date(2026, 1, 15))[-1] == "2025-12"
    # July/August take in the June annual revision.
    assert len(recent_periods(date(2026, 7, 10))) == 40


def test_all_periods() -> None:
    full = all_periods(date(2026, 9, 30))
    assert full[0] == "2013-01"
    assert full[-1] == "2026-08"
    assert len(full) == len(set(full)) == 164


def test_chunks_split_on_gaps_and_size() -> None:
    from src.collectors.census_trade import _chunks

    months = ["2024-11", "2024-12", "2025-01", "2025-02", "2025-05"]
    assert _chunks(months, 3) == [
        ("2024-11", "2025-01"), ("2025-02", "2025-02"), ("2025-05", "2025-05"),
    ]
    assert _chunks([], 12) == []


def test_parse_rows_spanning_a_date_range() -> None:
    rows = [IMPORT_PRODUCT_ROWS[0], IMPORT_PRODUCT_ROWS[1],
            IMPORT_PRODUCT_ROWS[1][:-3] + ["2026-08", "HS10", "-"]]
    df = parse_census_rows(rows, _IMPORT_PRODUCT_VARS)
    assert df["period_date"].to_list() == [date(2026, 7, 1), date(2026, 8, 1)]
    assert df["month"].to_list() == [7, 8]


@patch("src.collectors.census_trade.fetch_census")
def test_fetch_partners_queries_one_chapter_over_a_range(
    mock_fetch: MagicMock, mock_settings: None,
) -> None:
    from src.collectors.census_trade import fetch_partners

    mock_fetch.return_value = []
    fetch_partners("2013-01", "2017-12", "84")
    params = [c.args[1] for c in mock_fetch.call_args_list]
    assert [p.get("I_COMMODITY") or p.get("E_COMMODITY") for p in params] == ["84", "84"]
    assert {p["time"] for p in params} == {"from 2013-01 to 2017-12"}


@patch("src.collectors.census_trade.time.sleep")
@patch("src.collectors.census_trade.requests.get")
def test_fetch_census_retries_server_errors_without_leaking_key(
    mock_get: MagicMock, mock_sleep: MagicMock, mock_settings: None,
) -> None:
    from src.collectors.census_trade import fetch_census

    mock_get.return_value = MagicMock(status_code=500)
    with pytest.raises(RuntimeError) as err:
        fetch_census("exports/hs", {"COMM_LVL": "HS2"})
    assert mock_get.call_count == 3
    assert "test-key" not in str(err.value)


def _fake_partners(rows: object) -> object:
    import polars as pl

    return lambda start, end, chapter: rows if chapter == "10" else pl.DataFrame()


@patch("src.collectors.census_trade.write_raw")
@patch("src.collectors.census_trade.fetch_partners")
@patch("src.collectors.census_trade.fetch_products")
def test_collect_explicit_periods_chunks_and_writes_once_per_range(
    mock_products: MagicMock,
    mock_partners: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.collectors.census_trade import HS2_CHAPTERS, collect_trade_data
    from src.storage.writer import init_db

    init_db()
    periods = [f"{y}-{m:02d}" for y in (2024, 2025) for m in range(1, 13)] + ["2026-01"]
    mock_products.return_value = parse_census_rows(IMPORT_PRODUCT_ROWS, _IMPORT_PRODUCT_VARS)
    mock_partners.side_effect = _fake_partners(
        parse_census_rows(EXPORT_PARTNER_ROWS, _EXPORT_PARTNER_VARS)
    )
    mock_write.side_effect = lambda source, df, table_name: df.height

    count = collect_trade_data(periods=periods)
    assert [c.args for c in mock_products.call_args_list] == [
        ("2024-01", "2024-12"), ("2025-01", "2025-12"), ("2026-01", "2026-01"),
    ]
    # 25 months -> two 24-month-max partner ranges, every chapter in each.
    assert mock_partners.call_count == 2 * len(HS2_CHAPTERS)
    assert count == 3 * 2 + 2 * 5  # fetch_partners is mocked, so rows are unfiltered
    writes = [(c.args[0], c.kwargs["table_name"]) for c in mock_write.call_args_list]
    # Each table gets its own parquet directory: sharing one would overwrite.
    assert writes == [("census_trade_products", "us_trade_products")] * 3 + [
        ("census_trade_partners", "us_trade_partners")
    ] * 2


@patch("src.collectors.census_trade.date")
@patch("src.collectors.census_trade._stored_periods")
@patch("src.collectors.census_trade.write_raw")
@patch("src.collectors.census_trade.fetch_partners")
@patch("src.collectors.census_trade.fetch_products")
def test_backfill_fills_only_gaps_newest_first(
    mock_products: MagicMock,
    mock_partners: MagicMock,
    mock_write: MagicMock,
    mock_stored: MagicMock,
    mock_date: MagicMock,
    mock_settings: None,
) -> None:
    import polars as pl

    from src.collectors.census_trade import all_periods, collect_trade_data

    mock_date.today.return_value = date(2026, 9, 30)
    full = all_periods(date(2026, 9, 30))
    # Everything stored except 2013 and 2020.
    mock_stored.return_value = {p for p in full if p[:4] not in ("2013", "2020")}
    mock_products.return_value = pl.DataFrame()
    mock_partners.return_value = pl.DataFrame()

    collect_trade_data(bulk_backfill=True)
    assert [c.args for c in mock_products.call_args_list] == [
        ("2026-05", "2026-08"), ("2020-01", "2020-12"), ("2013-01", "2013-12"),
    ]


@patch("src.collectors.census_trade.BACKFILL_BUDGET_SECONDS", -1)
@patch("src.collectors.census_trade.date")
@patch("src.collectors.census_trade._stored_periods", return_value=set())
@patch("src.collectors.census_trade.write_raw")
@patch("src.collectors.census_trade.fetch_partners")
@patch("src.collectors.census_trade.fetch_products")
def test_spent_budget_still_refreshes_recent_window(
    mock_products: MagicMock,
    mock_partners: MagicMock,
    mock_write: MagicMock,
    mock_stored: MagicMock,
    mock_date: MagicMock,
    mock_settings: None,
) -> None:
    import polars as pl

    from src.collectors.census_trade import HS2_CHAPTERS, collect_trade_data

    mock_date.today.return_value = date(2026, 9, 30)
    mock_products.return_value = pl.DataFrame()
    mock_partners.return_value = pl.DataFrame()

    collect_trade_data(bulk_backfill=True)
    assert [c.args for c in mock_products.call_args_list] == [("2026-05", "2026-08")]
    assert mock_partners.call_count == len(HS2_CHAPTERS)  # recent window only
