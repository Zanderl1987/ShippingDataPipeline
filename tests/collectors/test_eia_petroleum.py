from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.collectors.eia_petroleum import (
    _parse_eia_response,
    _parse_eia_stocks_response,
    get_monthly_imports_by_country,
    get_weekly_stocks,
    get_weekly_supply,
)


@pytest.fixture
def mock_eia_stocks_response() -> dict:
    return {
        "response": {
            "data": [
                {
                    "period": "20260718",
                    "product": ["EPC0"],
                    "du": ["NUS"],
                    "value": 411675,
                    "units": "MBBL",
                },
                {
                    "period": "20260711",
                    "product": ["EPC0"],
                    "du": ["NUS"],
                    "value": 409665,
                    "units": "MBBL",
                },
                {
                    "period": "20260704",
                    "product": ["EPM0"],
                    "du": ["NUS"],
                    "value": 212062,
                    "units": "MBBL",
                },
            ]
        }
    }


@pytest.fixture
def mock_eia_supply_response() -> dict:
    return {
        "response": {
            "data": [
                {
                    "period": "20260718",
                    "product": ["WCRFPUS2"],
                    "du": ["NUS"],
                    "value": 13.4,
                    "units": "MBBL/D",
                },
                {
                    "period": "20260711",
                    "product": ["WCRFPUS2"],
                    "du": ["NUS"],
                    "value": 13.2,
                    "units": "MBBL/D",
                },
            ]
        }
    }


class TestParseEiaStocksResponse:
    def test_parses_stocks(self, mock_eia_stocks_response: dict) -> None:
        df = _parse_eia_stocks_response(mock_eia_stocks_response)
        assert df.height == 3
        assert "report_date" in df.columns
        assert "product" in df.columns
        assert "area" in df.columns
        assert "source" in df.columns
        assert "partition_date" in df.columns

    def test_dates_formatted(self, mock_eia_stocks_response: dict) -> None:
        df = _parse_eia_stocks_response(mock_eia_stocks_response)
        dates = df["report_date"].to_list()
        assert "2026-07-18" in dates
        assert "2026-07-11" in dates

    def test_values_cast_float(self, mock_eia_stocks_response: dict) -> None:
        df = _parse_eia_stocks_response(mock_eia_stocks_response)
        assert df["value_thousand_bbl"].dtype == pl.Float64

    def test_empty_response(self) -> None:
        df = _parse_eia_stocks_response({"response": {"data": []}})
        assert df.height == 0

    def test_source_column(self, mock_eia_stocks_response: dict) -> None:
        df = _parse_eia_stocks_response(mock_eia_stocks_response)
        assert df["source"][0] == "eia_petroleum"


class TestParseEiaResponse:
    def test_parses_supply(self, mock_eia_supply_response: dict) -> None:
        df = _parse_eia_response(mock_eia_supply_response, frequency="weekly")
        assert df.height == 2
        assert "period" in df.columns
        assert "product" in df.columns
        assert "value" in df.columns
        assert "source" in df.columns

    def test_empty_response(self) -> None:
        df = _parse_eia_response({"response": {"data": []}})
        assert df.height == 0


class TestGetEiaData:
    @patch("src.collectors.eia_petroleum._get_api_key", return_value="test_key")
    @patch("src.collectors.eia_petroleum.get_with_retry")
    def test_returns_json(
        self, mock_get: MagicMock, mock_key: MagicMock,
        mock_eia_stocks_response: dict,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_eia_stocks_response
        mock_get.return_value = mock_resp

        result = get_weekly_stocks()
        assert "response" in result
        assert len(result["response"]["data"]) == 3

    @patch("src.collectors.eia_petroleum._get_api_key", return_value="test_key")
    @patch("src.collectors.eia_petroleum.get_with_retry")
    def test_raises_on_error(self, mock_get: MagicMock, mock_key: MagicMock) -> None:
        mock_get.side_effect = Exception("Connection failed")
        with pytest.raises(Exception, match="Connection failed"):
            get_weekly_stocks()

    @patch("src.collectors.eia_petroleum._get_api_key", return_value="test_key")
    @patch("src.collectors.eia_petroleum.get_with_retry")
    def test_supply_endpoint(
        self, mock_get: MagicMock, mock_key: MagicMock,
        mock_eia_supply_response: dict,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_eia_supply_response
        mock_get.return_value = mock_resp

        result = get_weekly_supply()
        assert "response" in result

    @patch("src.collectors.eia_petroleum._get_api_key", return_value="test_key")
    @patch("src.collectors.eia_petroleum.get_with_retry")
    def test_imports_endpoint(
        self, mock_get: MagicMock, mock_key: MagicMock,
        mock_eia_supply_response: dict,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_eia_supply_response
        mock_get.return_value = mock_resp

        result = get_monthly_imports_by_country()
        assert "response" in result


def _v2(period: str, product: str, process: str, value: int) -> dict:
    return {"period": period, "duoarea": "NUS", "area-name": "U.S.", "product": product,
            "product-name": product, "process-name": process, "value": str(value),
            "units": "MBBL"}


def test_crude_stocks_split_into_commercial_spr_and_total() -> None:
    week = "2026-09-18"
    raw = {"response": {"data": [
        _v2(week, "EPC0", "Ending Stocks Excluding SPR", 400),
        _v2(week, "EPC0", "Ending Stocks SPR", 410),
        _v2(week, "EPC0", "Ending Stocks", 810),
        _v2(week, "EPM0", "Ending Stocks", 200),  # gasoline: no SPR split
    ]}}
    df = _parse_eia_stocks_response(raw)
    kinds = dict(zip(df["value_thousand_bbl"], df["stock_type"], strict=True))
    assert kinds == {400.0: "commercial", 410.0: "spr", 810.0: "total", 200.0: "commercial"}


@patch("src.collectors.eia_petroleum.get_petroleum_series")
def test_get_all_weekly_stocks_pages_until_total(mock_series: MagicMock) -> None:
    from src.collectors.eia_petroleum import PAGE_ROWS, get_all_weekly_stocks

    rows = [{"period": str(i)} for i in range(PAGE_ROWS + 3)]
    mock_series.side_effect = lambda **kw: {"response": {
        "total": len(rows), "data": rows[kw["offset"]:kw["offset"] + kw["length"]]}}
    out = get_all_weekly_stocks()
    assert len(out["response"]["data"]) == len(rows)
    assert [c.kwargs["offset"] for c in mock_series.call_args_list] == [0, PAGE_ROWS]


@pytest.mark.parametrize(("earliest", "bulk", "backfill"), [
    (None, True, True),
    ("2026-08-14", True, True),   # only recent weeks stored
    ("1982-08-20", True, False),  # history already there
    (None, False, False),         # local runs never backfill
])
def test_weekly_stocks_backfills_only_in_ci_when_history_missing(
    earliest: str | None, bulk: bool, backfill: bool,
) -> None:
    from datetime import date

    from src.collectors import eia_petroleum as eia

    day = date.fromisoformat(earliest) if earliest else None
    with patch.object(eia, "_earliest_stock_date", return_value=day), \
            patch.object(eia, "get_all_weekly_stocks", return_value={}) as full, \
            patch.object(eia, "get_weekly_stocks", return_value={}) as recent:
        eia.collect_weekly_stocks(tracker=MagicMock(), bulk_backfill=bulk)
    assert full.called is backfill and recent.called is not backfill


def test_other_stock_sub_series_get_their_own_label() -> None:
    raw = {"response": {"data": [
        _v2("2026-09-18", "EPC0", "Ending Stocks Excluding SPR", 400),
        _v2("2026-09-18", "EPC0", "Stocks in Transit (on Ships) from Alaska", 4),
    ]}}
    df = _parse_eia_stocks_response(raw)
    assert sorted(df["stock_type"]) == ["commercial", "stocks_in_transit_on_ships_from_alaska"]


def test_backfill_replaces_old_mislabelled_rows(tmp_path) -> None:  # noqa: ANN001
    from src.collectors import eia_petroleum as eia
    from src.config import settings
    from src.storage.writer import get_connection, init_db, write_raw

    old_data, old_storage = settings.data_dir, settings.storage_dir
    settings.data_dir, settings.storage_dir = tmp_path / "data", tmp_path / "storage"
    settings.ensure_dirs()
    try:
        init_db()
        week = "2026-09-18"
        # What the old parser stored: a bulk-terminal series as "commercial".
        write_raw(eia.SOURCE, eia._parse_eia_stocks_response({"response": {"data": [
            _v2(week, "EPLLPA", "", 7)]}}), table_name="oil_inventories")
        history = {"response": {"data": [
            _v2(week, "EPC0", "Ending Stocks Excluding SPR", 400),
            _v2(week, "EPLLPA", "Stocks at Bulk Terminals", 7),
        ]}}
        with patch.object(eia, "get_all_weekly_stocks", return_value=history):
            eia.collect_weekly_stocks(tracker=MagicMock(), bulk_backfill=True)
        conn = get_connection()
        rows = conn.execute("SELECT product, stock_type FROM oil_inventories "
                            "ORDER BY 1").fetchall()
        conn.close()
        assert rows == [("EPC0", "commercial"), ("EPLLPA", "stocks_at_bulk_terminals")]
    finally:
        settings.data_dir, settings.storage_dir = old_data, old_storage
