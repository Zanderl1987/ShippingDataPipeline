from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.collectors.tankermap import (
    _parse_port_calls,
    _parse_vessels,
    get_live_vessels,
    get_port_calls,
)


@pytest.fixture
def mock_vessels_response() -> dict:
    return {
        "vessels": [
            {
                "imo": 9876543,
                "mmsi": 538008414,
                "name": "MARAN PROGRESS",
                "lat": 1.405,
                "lon": 104.121,
                "sog": 11.5,
                "cog": 245.0,
                "heading": 244,
                "status": "Under Way Using Engine",
                "draught": 12.4,
                "destination": "FUJAIRAH",
                "eta": "2026-06-15T22:00Z",
                "timestamp": "2026-07-23T12:00:00Z",
            },
            {
                "imo": 9995973,
                "mmsi": 538098765,
                "name": "ENERGY DIONE",
                "lat": 26.2,
                "lon": 50.5,
                "sog": 14.2,
                "cog": 180.0,
                "heading": 179,
                "status": "Under Way Using Engine",
                "draught": 15.8,
                "destination": "RAS TANURA",
                "eta": "2026-07-25T08:00Z",
                "timestamp": "2026-07-23T12:30:00Z",
            },
        ]
    }


@pytest.fixture
def mock_port_calls_response() -> dict:
    return {
        "calls": [
            {
                "imo": 9876543,
                "mmsi": 538008414,
                "vessel_name": "MARAN PROGRESS",
                "port_unlocode": "SGSIN",
                "port_name": "Singapore",
                "country": "Singapore",
                "event_type": "arrival",
                "timestamp": "2026-07-20T08:00:00Z",
                "eta": "2026-07-20T07:30:00Z",
                "etd": "2026-07-21T14:00:00Z",
                "previous_port": "FUJAIRAH",
                "next_port": "ROTTERDAM",
            },
            {
                "imo": 9995973,
                "mmsi": 538098765,
                "vessel_name": "ENERGY DIONE",
                "port_unlocode": "SAJUB",
                "port_name": "Jubail",
                "country": "Saudi Arabia",
                "event_type": "departure",
                "timestamp": "2026-07-19T16:00:00Z",
                "eta": None,
                "etd": None,
                "previous_port": None,
                "next_port": "SINGAPORE",
            },
        ]
    }


class TestParseVessels:
    def test_parses_vessels(self, mock_vessels_response: dict) -> None:
        df = _parse_vessels(mock_vessels_response)
        assert df.height == 2
        assert "imo" in df.columns
        assert "mmsi" in df.columns
        assert "vessel_name" in df.columns
        assert "latitude" in df.columns
        assert "longitude" in df.columns
        assert "sog" in df.columns
        assert "source" in df.columns
        assert "partition_date" in df.columns

    def test_vessel_names(self, mock_vessels_response: dict) -> None:
        df = _parse_vessels(mock_vessels_response)
        names = df["vessel_name"].to_list()
        assert "MARAN PROGRESS" in names
        assert "ENERGY DIONE" in names

    def test_imo_values(self, mock_vessels_response: dict) -> None:
        df = _parse_vessels(mock_vessels_response)
        imos = df["imo"].to_list()
        assert 9876543 in imos
        assert 9995973 in imos

    def test_positions(self, mock_vessels_response: dict) -> None:
        df = _parse_vessels(mock_vessels_response)
        assert df["latitude"][0] == pytest.approx(1.405, abs=0.001)
        assert df["longitude"][0] == pytest.approx(104.121, abs=0.001)

    def test_empty_response(self) -> None:
        df = _parse_vessels({"vessels": []})
        assert df.height == 0

    def test_source_column(self, mock_vessels_response: dict) -> None:
        df = _parse_vessels(mock_vessels_response)
        assert df["source"][0] == "tankermap"


class TestParsePortCalls:
    def test_parses_calls(self, mock_port_calls_response: dict) -> None:
        df = _parse_port_calls(mock_port_calls_response)
        assert df.height == 2
        assert "imo" in df.columns
        assert "port_unlocode" in df.columns
        assert "port_name" in df.columns
        assert "event_type" in df.columns
        assert "source" in df.columns
        assert "partition_date" in df.columns

    def test_port_data(self, mock_port_calls_response: dict) -> None:
        df = _parse_port_calls(mock_port_calls_response)
        ports = df["port_name"].to_list()
        assert "Singapore" in ports
        assert "Jubail" in ports

    def test_empty_response(self) -> None:
        df = _parse_port_calls({"calls": []})
        assert df.height == 0

    def test_source_column(self, mock_port_calls_response: dict) -> None:
        df = _parse_port_calls(mock_port_calls_response)
        assert df["source"][0] == "tankermap"


class TestGetLiveData:
    @patch("src.collectors.tankermap.requests.get")
    def test_returns_json(self, mock_get: MagicMock, mock_vessels_response: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_vessels_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_live_vessels()
        assert "vessels" in result
        assert len(result["vessels"]) == 2

    @patch("src.collectors.tankermap.requests.get")
    def test_raises_on_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("Connection failed")
        with pytest.raises(Exception, match="Connection failed"):
            get_live_vessels()

    @patch("src.collectors.tankermap.requests.get")
    def test_port_calls(self, mock_get: MagicMock, mock_port_calls_response: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_port_calls_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_port_calls()
        assert "calls" in result
