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
    @patch("src.collectors.eia_petroleum.requests.get")
    def test_returns_json(
        self, mock_get: MagicMock, mock_key: MagicMock,
        mock_eia_stocks_response: dict,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_eia_stocks_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_weekly_stocks()
        assert "response" in result
        assert len(result["response"]["data"]) == 3

    @patch("src.collectors.eia_petroleum._get_api_key", return_value="test_key")
    @patch("src.collectors.eia_petroleum.requests.get")
    def test_raises_on_error(self, mock_get: MagicMock, mock_key: MagicMock) -> None:
        mock_get.side_effect = Exception("Connection failed")
        with pytest.raises(Exception, match="Connection failed"):
            get_weekly_stocks()

    @patch("src.collectors.eia_petroleum._get_api_key", return_value="test_key")
    @patch("src.collectors.eia_petroleum.requests.get")
    def test_supply_endpoint(
        self, mock_get: MagicMock, mock_key: MagicMock,
        mock_eia_supply_response: dict,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_eia_supply_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_weekly_supply()
        assert "response" in result

    @patch("src.collectors.eia_petroleum._get_api_key", return_value="test_key")
    @patch("src.collectors.eia_petroleum.requests.get")
    def test_imports_endpoint(
        self, mock_get: MagicMock, mock_key: MagicMock,
        mock_eia_supply_response: dict,
    ) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_eia_supply_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_monthly_imports_by_country()
        assert "response" in result
