from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.collectors.digitraffic import (
    _NAV_STATUS,
    _parse_locations,
    _parse_port_calls,
    _parse_ports,
    _parse_vessels,
    fetch_locations,
    fetch_port_calls,
    fetch_ports,
    fetch_vessels,
)


def make_location_feature(
    mmsi: int, lon: float, lat: float, *, ts_external: int = 1786303151034
) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "mmsi": mmsi,
            "sog": 11.5,
            "cog": 245.0,
            "heading": 244,
            "navStat": 0,
            "rot": 0,
            "posAcc": 1,
            "raim": 0,
            "timestamp": 9,
            "timestampExternal": ts_external,
        },
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


@pytest.fixture
def mock_locations() -> dict:
    return {
        "features": [
            make_location_feature(230011480, 21.074577, 60.515835),
            make_location_feature(246521000, 22.3, 61.1),
        ]
    }


@pytest.fixture
def mock_vessels() -> list[dict]:
    return [
        {
            "mmsi": 246521000,
            "imo": 9307358,
            "name": "TIMCA",
            "shipType": 70,
            "callSign": "PHFL",
            "destination": "BEANR",
            "draught": 75,
            "posType": 1,
            "timestamp": 1786217055998,
        },
    ]


@pytest.fixture
def mock_port_calls() -> dict:
    return {
        "portCalls": [
            {
                "portCallId": 3319462,
                "portCallTimestamp": "2026-08-09T10:57:07.000Z",
                "portToVisit": "FIECK",
                "prevPort": "SEGRH",
                "nextPort": "SEGRH",
                "vesselName": "Ecker\u00f6",
                "imoLloyds": 7633155,
                "mmsi": 266308000,
                "nationality": "SE",
                "vesselTypeCode": 20,
            },
        ]
    }


@pytest.fixture
def mock_ports() -> dict:
    return {
        "ssnLocations": {
            "type": "FeatureCollection",
            "dataUpdatedTime": "2026-08-09T10:00:00.000Z",
            "features": [
                {
                    "locode": "DEHEI",
                    "type": "Feature",
                    "geometry": {"type": "Point", "coordinates": [8.7, 49.41667]},
                    "properties": {
                        "locode": "DEHEI",
                        "locationName": "Heidelberg",
                        "country": "Germany",
                    },
                },
                {
                    "locode": "DEHEN",
                    "type": "Feature",
                    "geometry": None,
                    "properties": {
                        "locode": "DEHEN",
                        "locationName": "Heilbronn",
                        "country": "Germany",
                    },
                },
            ],
        }
    }


class TestParseLocations:
    def test_parses_features(self, mock_locations: dict, mock_vessels: list) -> None:
        df = _parse_locations(mock_locations, mock_vessels)
        assert df.height == 2
        for col in (
            "mmsi",
            "imo",
            "vessel_name",
            "latitude",
            "longitude",
            "sog",
            "cog",
            "heading",
            "nav_status",
            "draught",
            "timestamp",
            "source",
            "partition_date",
        ):
            assert col in df.columns

    def test_coordinates(self, mock_locations: dict, mock_vessels: list) -> None:
        df = _parse_locations(mock_locations, mock_vessels)
        assert df["longitude"][0] == pytest.approx(21.074577, abs=1e-6)
        assert df["latitude"][0] == pytest.approx(60.515835, abs=1e-6)

    def test_joins_vessel_metadata(self, mock_locations: dict, mock_vessels: list) -> None:
        # The vessel without metadata keeps null imo; the joined one gets it.
        df = _parse_locations(mock_locations, mock_vessels)
        imos = df["imo"].to_list()
        assert 9307358 in imos
        names = df["vessel_name"].to_list()
        assert "TIMCA" in names

    def test_nav_status_mapping(self, mock_locations: dict, mock_vessels: list) -> None:
        df = _parse_locations(mock_locations, mock_vessels)
        assert df["nav_status"][0] == _NAV_STATUS[0]

    def test_draught_decimeters_to_meters(
        self, mock_locations: dict, mock_vessels: list
    ) -> None:
        df = _parse_locations(mock_locations, mock_vessels)
        timca = df.filter(df["mmsi"] == 246521000)
        assert timca["draught"][0] == pytest.approx(7.5, abs=0.01)

    def test_epoch_ms_to_timestamp(self, mock_locations: dict, mock_vessels: list) -> None:
        df = _parse_locations(mock_locations, mock_vessels)
        assert df["timestamp"][0].startswith("2026-08-09T")

    def test_empty_response(self, mock_vessels: list) -> None:
        df = _parse_locations({"features": []}, mock_vessels)
        assert df.height == 0


class TestParseVessels:
    def test_parses_vessels(self, mock_vessels: list) -> None:
        df = _parse_vessels(mock_vessels)
        assert df.height == 1
        for col in ("imo", "mmsi", "vessel_name", "vessel_type", "callsign", "source"):
            assert col in df.columns

    def test_values(self, mock_vessels: list) -> None:
        df = _parse_vessels(mock_vessels)
        assert df["mmsi"][0] == 246521000
        assert df["imo"][0] == 9307358
        assert df["vessel_name"][0] == "TIMCA"
        assert df["source"][0] == "digitraffic"

    def test_empty_response(self) -> None:
        df = _parse_vessels([])
        assert df.height == 0


class TestParsePortCalls:
    def test_parses_calls(self, mock_port_calls: dict) -> None:
        df = _parse_port_calls(mock_port_calls["portCalls"])
        assert df.height == 1
        for col in (
            "imo",
            "mmsi",
            "vessel_name",
            "port_unlocode",
            "country",
            "event_type",
            "event_timestamp",
            "previous_port",
            "next_port",
            "source",
            "partition_date",
        ):
            assert col in df.columns

    def test_values(self, mock_port_calls: dict) -> None:
        df = _parse_port_calls(mock_port_calls["portCalls"])
        assert df["port_unlocode"][0] == "FIECK"
        assert df["vessel_name"][0] == "Ecker\u00f6"
        assert df["event_timestamp"][0] == "2026-08-09T10:57:07.000Z"
        assert df["source"][0] == "digitraffic"

    def test_empty_response(self) -> None:
        df = _parse_port_calls([])
        assert df.height == 0


class TestParsePorts:
    def test_parses_locations(self, mock_ports: dict) -> None:
        df = _parse_ports(mock_ports["ssnLocations"]["features"])
        assert df.height == 1
        for col in (
            "unlocode",
            "port_name",
            "country",
            "country_code",
            "latitude",
            "longitude",
            "source",
        ):
            assert col in df.columns

    def test_values(self, mock_ports: dict) -> None:
        df = _parse_ports(mock_ports["ssnLocations"]["features"])
        assert df["unlocode"][0] == "DEHEI"
        assert df["port_name"][0] == "Heidelberg"
        assert df["country"][0] == "Germany"
        assert df["country_code"][0] == "DE"
        assert df["latitude"][0] == pytest.approx(49.41667, abs=1e-5)
        assert df["longitude"][0] == pytest.approx(8.7, abs=1e-5)
        assert df["source"][0] == "digitraffic"

    def test_drops_locations_without_geometry(self, mock_ports: dict) -> None:
        df = _parse_ports(mock_ports["ssnLocations"]["features"])
        # Heilbronn (DEHEN) has geometry None and is dropped.
        assert "DEHEN" not in df["unlocode"].to_list()

    def test_empty_response(self) -> None:
        df = _parse_ports([])
        assert df.height == 0


class TestFetch:
    @patch("src.collectors.digitraffic.get_with_retry")
    def test_fetch_locations(self, mock_get: MagicMock, mock_locations: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_locations
        mock_get.return_value = mock_resp

        result = fetch_locations()
        assert "features" in result
        assert len(result["features"]) == 2

    @patch("src.collectors.digitraffic.get_with_retry")
    def test_fetch_vessels(self, mock_get: MagicMock, mock_vessels: list) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_vessels
        mock_get.return_value = mock_resp

        result = fetch_vessels()
        assert isinstance(result, list)
        assert len(result) == 1

    @patch("src.collectors.digitraffic.get_with_retry")
    def test_fetch_port_calls(self, mock_get: MagicMock, mock_port_calls: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_port_calls
        mock_get.return_value = mock_resp

        result = fetch_port_calls()
        assert isinstance(result, list)
        assert len(result) == 1

    @patch("src.collectors.digitraffic.get_with_retry")
    def test_fetch_ports(self, mock_get: MagicMock, mock_ports: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_ports
        mock_get.return_value = mock_resp

        result = fetch_ports()
        assert isinstance(result, list)
        assert len(result) == 2

    @patch("src.collectors.digitraffic.get_with_retry")
    def test_fetch_ports_malformed(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = ["not", "a", "dict"]
        mock_get.return_value = mock_resp

        assert fetch_ports() == []

    @patch("src.collectors.digitraffic.get_with_retry")
    def test_fetch_locations_raises(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("Connection failed")
        with pytest.raises(Exception, match="Connection failed"):
            fetch_locations()
