from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.collectors.imf_portwatch import (
    DAILY_CHOKEPOINTS_URL,
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
    """Paging goes through portwatch_ports.query_all (ordered by ObjectId,
    stops on exceededTransferLimit); this module only builds the filter."""

    @patch("src.collectors.imf_portwatch.query_all")
    def test_uses_ordered_pager(self, mock_query: MagicMock, mock_arcgis_response: dict) -> None:
        mock_query.return_value = mock_arcgis_response["features"]
        result = get_daily_chokepoint_data()
        assert result == {"features": mock_arcgis_response["features"]}
        mock_query.assert_called_once_with(DAILY_CHOKEPOINTS_URL, where="1=1")

    @patch("src.collectors.imf_portwatch.query_all")
    def test_date_filter_applies_to_every_chokepoint(self, mock_query: MagicMock) -> None:
        # Without brackets, "a OR b AND date >= x" limited only b by date.
        mock_query.return_value = []
        get_daily_chokepoint_data(
            chokepoint_ids=["chokepoint1", "chokepoint6"],
            start_date="2026-01-01",
            end_date="2026-01-31",
        )
        assert mock_query.call_args.kwargs["where"] == (
            "(portid = 'chokepoint1' OR portid = 'chokepoint6')"
            " AND date >= TIMESTAMP '2026-01-01 00:00:00'"
            " AND date <= TIMESTAMP '2026-01-31 23:59:59'"
        )

    @patch("src.collectors.imf_portwatch.query_all")
    def test_rejects_unsafe_chokepoint_id(self, mock_query: MagicMock) -> None:
        with pytest.raises(ValueError, match="chokepoint id"):
            get_daily_chokepoint_data(chokepoint_ids=["x' OR '1'='1"])
        mock_query.assert_not_called()

    @patch("src.collectors.imf_portwatch.query_all")
    def test_raises_on_error(self, mock_query: MagicMock) -> None:
        mock_query.side_effect = RuntimeError("ArcGIS query failed")
        with pytest.raises(RuntimeError, match="ArcGIS query failed"):
            get_daily_chokepoint_data()


class TestParseNamesAndDates:
    def test_name_comes_from_portwatch_not_a_local_table(self) -> None:
        # The old hardcoded map called CHOKEPOINT3 the Strait of Malacca;
        # PortWatch's chokepoint3 is the Bosporus.
        df = _parse_chokepoint_transits(
            {"features": [{"attributes": {"date": "2026-09-20", "portid": "CHOKEPOINT3",
                                          "portname": "Bosporus Strait"}}]}
        )
        assert df["chokepoint_name"].to_list() == ["Bosporus Strait"]

    def test_epoch_date_is_utc(self) -> None:
        # 2023-11-15 00:00 UTC is still the 14th in any US time zone.
        df = _parse_chokepoint_transits(
            {"features": [{"attributes": {"date": 1700006400000, "portid": "chokepoint1",
                                          "portname": "Suez Canal"}}]}
        )
        assert df["transit_date"].to_list() == ["2023-11-15"]
