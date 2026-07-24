from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.collectors.hormuz_monitor import (
    _parse_oil_prices,
    _parse_traffic,
    get_prices,
    get_risk,
    get_traffic,
)


@pytest.fixture
def mock_prices_response() -> dict:
    return {
        "status": "success",
        "delay_minutes": 15,
        "data": {
            "brent_usd": 121.40,
            "wti_usd": 117.80,
            "dubai_usd": 119.60,
            "lng_jkm_mmbtu": 38.20,
            "vlcc_td3c_ws": 280,
            "vlcc_td3c_tce_usd_day": 45000,
            "risk_premium_pct": 3.5,
            "td_change_pct": 5.2,
            "updated_at": "2026-07-23T14:17:00Z",
        },
    }


@pytest.fixture
def mock_traffic_response() -> dict:
    return {
        "status": "success",
        "data": {
            "transits_today": 34,
            "tanker_count": 18,
            "tanker_dwt": 900000,
            "total_capacity": 1400000,
            "reduction_vs_avg": 5.0,
            "inbound_lane": "open",
            "outbound_lane": "restricted",
        },
    }


@pytest.fixture
def mock_risk_response() -> dict:
    return {
        "status": "success",
        "data": {
            "composite_score": 7.8,
            "risk_level": "High",
            "trend": "rising",
            "dimensions": {
                "geopolitical": 8.5,
                "military": 7.2,
                "shipping": 6.9,
                "weather": 2.1,
                "infrastructure": 4.5,
            },
        },
    }


class TestParseOilPrices:
    def test_parses_prices(self, mock_prices_response: dict) -> None:
        df = _parse_oil_prices(mock_prices_response)
        assert df.height == 1
        assert "brent_usd" in df.columns
        assert "wti_usd" in df.columns
        assert "dubai_usd" in df.columns
        assert "vlcc_td3c_ws" in df.columns
        assert "source" in df.columns
        assert "price_date" in df.columns
        assert "partition_date" in df.columns

    def test_price_values(self, mock_prices_response: dict) -> None:
        df = _parse_oil_prices(mock_prices_response)
        assert df["brent_usd"][0] == pytest.approx(121.40, abs=0.01)
        assert df["wti_usd"][0] == pytest.approx(117.80, abs=0.01)
        assert df["vlcc_td3c_ws"][0] == 280

    def test_empty_response(self) -> None:
        df = _parse_oil_prices({})
        assert df.height == 0

    def test_source_column(self, mock_prices_response: dict) -> None:
        df = _parse_oil_prices(mock_prices_response)
        assert df["source"][0] == "hormuz_monitor"


class TestParseTraffic:
    def test_parses_traffic(self, mock_traffic_response: dict) -> None:
        df = _parse_traffic(mock_traffic_response)
        assert df.height == 1
        assert "chokepoint_id" in df.columns
        assert "chokepoint_name" in df.columns
        assert "n_tanker" in df.columns
        assert "capacity_tanker" in df.columns
        assert "source" in df.columns
        assert "partition_date" in df.columns

    def test_traffic_values(self, mock_traffic_response: dict) -> None:
        df = _parse_traffic(mock_traffic_response)
        assert df["n_tanker"][0] == 18
        assert df["capacity_tanker"][0] == 900000
        assert df["chokepoint_id"][0] == "strait-of-hormuz"

    def test_empty_response(self) -> None:
        df = _parse_traffic({})
        assert df.height == 0

    def test_source_column(self, mock_traffic_response: dict) -> None:
        df = _parse_traffic(mock_traffic_response)
        assert df["source"][0] == "hormuz_monitor"


class TestGetData:
    @patch("src.collectors.hormuz_monitor.requests.get")
    def test_get_prices(self, mock_get: MagicMock, mock_prices_response: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_prices_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_prices()
        assert result["status"] == "success"
        assert result["data"]["brent_usd"] == 121.40

    @patch("src.collectors.hormuz_monitor.requests.get")
    def test_get_traffic(self, mock_get: MagicMock, mock_traffic_response: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_traffic_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_traffic()
        assert result["status"] == "success"
        assert result["data"]["transits_today"] == 34

    @patch("src.collectors.hormuz_monitor.requests.get")
    def test_get_risk(self, mock_get: MagicMock, mock_risk_response: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_risk_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_risk()
        assert result["status"] == "success"
        assert result["data"]["composite_score"] == 7.8

    @patch("src.collectors.hormuz_monitor.requests.get")
    def test_raises_on_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("Connection failed")
        with pytest.raises(Exception, match="Connection failed"):
            get_prices()
