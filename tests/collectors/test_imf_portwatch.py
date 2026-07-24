from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.collectors.imf_portwatch import (
    _parse_chokepoint_transits,
    get_daily_chokepoint_data,
)


@pytest.fixture
def mock_arcgis_response() -> dict:
    return {
        "features": [
            {
                "attributes": {
                    "date": 1700000000000,
                    "portid": "CHOKEPOINT1",
                    "portname": "Suez Canal",
                    "n_container": 23,
                    "n_dry_bulk": 22,
                    "n_general_cargo": 14,
                    "n_roro": 4,
                    "n_tanker": 22,
                    "n_cargo": 63,
                    "n_total": 85,
                    "capacity_container": 1937886,
                    "capacity_dry_bulk": 943957,
                    "capacity_general_cargo": 174973,
                    "capacity_roro": 46461,
                    "capacity_tanker": 1326360,
                    "capacity_cargo": 3103276,
                    "capacity": 4429637,
                }
            },
            {
                "attributes": {
                    "date": 1700000000000,
                    "portid": "CHOKEPOINT5",
                    "portname": "Strait of Hormuz",
                    "n_container": 5,
                    "n_dry_bulk": 8,
                    "n_general_cargo": 3,
                    "n_roro": 1,
                    "n_tanker": 18,
                    "n_cargo": 29,
                    "n_total": 34,
                    "capacity_container": 200000,
                    "capacity_dry_bulk": 300000,
                    "capacity_general_cargo": 30000,
                    "capacity_roro": 10000,
                    "capacity_tanker": 900000,
                    "capacity_cargo": 1200000,
                    "capacity": 1400000,
                }
            },
        ]
    }


@pytest.fixture
def mock_chokepoint_info() -> dict:
    return {
        "features": [
            {
                "attributes": {
                    "portid": "CHOKEPOINT1",
                    "portname": "Suez Canal",
                    "lat": 30.5,
                    "lon": 32.3,
                }
            },
        ]
    }


class TestParseChokepointTransits:
    def test_parses_features(self, mock_arcgis_response: dict) -> None:
        df = _parse_chokepoint_transits(mock_arcgis_response)
        assert df.height == 2
        assert "transit_date" in df.columns
        assert "chokepoint_id" in df.columns
        assert "chokepoint_name" in df.columns
        assert "n_tanker" in df.columns
        assert "capacity_tanker" in df.columns
        assert "source" in df.columns
        assert "partition_date" in df.columns

    def test_chokepoint_names(self, mock_arcgis_response: dict) -> None:
        df = _parse_chokepoint_transits(mock_arcgis_response)
        names = df["chokepoint_name"].to_list()
        assert "Suez Canal" in names
        assert "Strait of Hormuz" in names

    def test_tanker_counts(self, mock_arcgis_response: dict) -> None:
        df = _parse_chokepoint_transits(mock_arcgis_response)
        suez = df.filter(pl.col("chokepoint_id") == "CHOKEPOINT1")
        assert suez["n_tanker"][0] == 22
        assert suez["capacity_tanker"][0] == 1326360

    def test_empty_response(self) -> None:
        df = _parse_chokepoint_transits({"features": []})
        assert df.height == 0

    def test_source_column(self, mock_arcgis_response: dict) -> None:
        df = _parse_chokepoint_transits(mock_arcgis_response)
        assert df["source"][0] == "imf_portwatch"


class TestGetChokepointData:
    @patch("src.collectors.imf_portwatch.requests.get")
    def test_returns_json(self, mock_get: MagicMock, mock_arcgis_response: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_arcgis_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_daily_chokepoint_data()
        assert "features" in result
        assert len(result["features"]) == 2

    @patch("src.collectors.imf_portwatch.requests.get")
    def test_raises_on_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("Connection failed")
        with pytest.raises(Exception, match="Connection failed"):
            get_daily_chokepoint_data()

    @patch("src.collectors.imf_portwatch.requests.get")
    def test_chokepoint_filter(self, mock_get: MagicMock, mock_arcgis_response: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_arcgis_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        get_daily_chokepoint_data(chokepoint_ids=["CHOKEPOINT1"])
        call_args = mock_get.call_args
        assert "CHOKEPOINT1" in call_args[1]["params"]["where"]
