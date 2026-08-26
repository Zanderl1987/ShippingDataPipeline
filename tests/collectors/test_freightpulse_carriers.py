"""Tests for FreightPulse carriers collector."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl

from src.collectors.freightpulse_carriers import (
    SOURCE,
    _parse_carriers,
    _safe_float,
    _safe_int,
    collect_carriers,
)

# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------

class TestSafeFloat:
    def test_normal(self) -> None:
        assert _safe_float(42) == 42.0

    def test_string_number(self) -> None:
        assert _safe_float("3.6") == 3.6

    def test_none(self) -> None:
        assert _safe_float(None) is None

    def test_bad_string(self) -> None:
        assert _safe_float("abc") is None


class TestSafeInt:
    def test_normal(self) -> None:
        assert _safe_int(708) == 708

    def test_string_number(self) -> None:
        assert _safe_int("12000") == 12000

    def test_none(self) -> None:
        assert _safe_int(None) is None

    def test_bad_string(self) -> None:
        assert _safe_int("abc") is None


# ---------------------------------------------------------------------------
# Parser tests
# ---------------------------------------------------------------------------

SAMPLE_RESPONSE: dict = {
    "success": True,
    "data": {
        "timestamp": "2026-08-26T10:10:11.423Z",
        "source": "Carrier Performance Database",
        "data": {
            "ocean": [
                {
                    "name": "Maersk",
                    "code": "MAEU",
                    "country": "DK",
                    "fleet_teu": 4300000,
                    "vessels": 708,
                    "reliability_score": 72,
                    "market_share": 17.1,
                    "on_time_performance": 74,
                    "avg_transit_delay_hours": 22,
                    "customer_rating": "3.6",
                },
                {
                    "name": "MSC",
                    "code": "MSCU",
                    "country": "CH",
                    "fleet_teu": 4600000,
                    "vessels": 760,
                    "reliability_score": 68,
                    "market_share": 18.5,
                    "on_time_performance": 69,
                    "avg_transit_delay_hours": 26,
                    "customer_rating": "3.4",
                },
            ],
            "trucking": [
                {
                    "name": "J.B. Hunt",
                    "code": "JBHT",
                    "country": "US",
                    "fleet_size": 12000,
                    "reliability_score": 85,
                    "coverage": "National",
                    "on_time_performance": 83,
                    "avg_transit_delay_hours": 8,
                    "customer_rating": "4.3",
                },
            ],
            "air": [
                {
                    "name": "FedEx",
                    "code": "FDX",
                    "country": "US",
                    "fleet_size": 680,
                    "reliability_score": 89,
                    "market_share": 24.5,
                    "on_time_performance": 90,
                    "avg_transit_delay_hours": 3,
                    "customer_rating": "4.5",
                },
            ],
        },
    },
}


class TestParseCarriers:
    def test_parses_all_types(self) -> None:
        df = _parse_carriers(SAMPLE_RESPONSE)
        assert df.height == 4  # 2 ocean + 1 trucking + 1 air

    def test_ocean_fields(self) -> None:
        df = _parse_carriers(SAMPLE_RESPONSE)
        ocean = df.filter(pl.col("carrier_type") == "ocean")
        assert ocean.height == 2
        maersk = ocean.filter(pl.col("carrier_code") == "MAEU").to_dicts()[0]
        assert maersk["carrier_name"] == "Maersk"
        assert maersk["fleet_size"] == 4300000
        assert maersk["fleet_size_unit"] == "teu"
        assert maersk["vehicle_count"] == 708
        assert maersk["reliability_score"] == 72.0
        assert maersk["market_share_pct"] == 17.1
        assert maersk["customer_rating"] == 3.6

    def test_trucking_fields(self) -> None:
        df = _parse_carriers(SAMPLE_RESPONSE)
        truck = df.filter(pl.col("carrier_code") == "JBHT").to_dicts()[0]
        assert truck["carrier_type"] == "trucking"
        assert truck["fleet_size"] == 12000
        assert truck["fleet_size_unit"] == "vehicles"
        assert truck["vehicle_count"] is None
        assert truck["market_share_pct"] is None

    def test_air_fields(self) -> None:
        df = _parse_carriers(SAMPLE_RESPONSE)
        air = df.filter(pl.col("carrier_code") == "FDX").to_dicts()[0]
        assert air["carrier_type"] == "air"
        assert air["fleet_size"] == 680
        assert air["fleet_size_unit"] == "aircraft"
        assert air["market_share_pct"] == 24.5

    def test_snapshot_date(self) -> None:
        df = _parse_carriers(SAMPLE_RESPONSE)
        assert df["snapshot_date"][0] is not None

    def test_empty_response(self) -> None:
        df = _parse_carriers({"data": {"data": {}}})
        assert df.height == 0

    def test_missing_inner_data(self) -> None:
        df = _parse_carriers({})
        assert df.height == 0

    def test_carriers_without_code_skipped(self) -> None:
        resp = {
            "data": {
                "data": {
                    "ocean": [{"name": "No Code Carrier"}],
                }
            }
        }
        df = _parse_carriers(resp)
        assert df.height == 0


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------

class TestCollectCarriers:
    @patch("src.collectors.freightpulse_carriers.get_with_retry")
    @patch("src.collectors.freightpulse_carriers.write_raw")
    def test_collect_carriers(self, mock_write: MagicMock, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_RESPONSE
        mock_get.return_value = mock_resp
        mock_write.return_value = 4

        tracker = MagicMock()
        count = collect_carriers(tracker=tracker)

        assert count == 4
        mock_get.assert_called_once()
        write_args = mock_write.call_args
        assert write_args[0][0] == SOURCE
        assert write_args[1]["table_name"] == "carriers"

    @patch("src.collectors.freightpulse_carriers.get_with_retry")
    def test_collect_carriers_empty(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"data": {"data": {}}}
        mock_get.return_value = mock_resp

        tracker = MagicMock()
        count = collect_carriers(tracker=tracker)
        assert count == 0
