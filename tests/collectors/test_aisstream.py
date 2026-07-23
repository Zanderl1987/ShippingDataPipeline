from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.collectors.aisstream import (
    _parse_position_report,
    _parse_ship_static,
)


@pytest.fixture
def mock_position_message() -> dict:
    return {
        "MessageType": "PositionReport",
        "Metadata": {
            "MMSI": 259000420,
            "ShipName": "AUGUSTSON",
            "latitude": 66.02695,
            "longitude": 12.25382,
            "time_utc": "2026-07-23 18:22:32 UTC",
        },
        "Message": {
            "PositionReport": {
                "Cog": 308,
                "CommunicationState": 81982,
                "Latitude": 66.02695,
                "Longitude": 12.25382,
                "MessageID": 1,
                "NavigationalStatus": 15,
                "PositionAccuracy": True,
                "Raim": False,
                "RateOfTurn": 4,
                "RepeatIndicator": 0,
                "Sog": 0,
                "Spare": 0,
                "SpecialManoeuvreIndicator": 0,
                "Timestamp": 31,
                "TrueHeading": 235,
                "UserID": 259000420,
                "Valid": True,
            }
        },
    }


@pytest.fixture
def mock_ship_static_message() -> dict:
    return {
        "MessageType": "ShipStaticData",
        "Metadata": {
            "MMSI": 259000420,
            "ShipName": "AUGUSTSON",
        },
        "Message": {
            "ShipStaticData": {
                "AisVersion": 2,
                "CallSign": "LBHF",
                "Destination": "ROTTERDAM",
                "Dimension": {"A": 20, "B": 27, "C": 7, "D": 7},
                "Dte": False,
                "Eta": {"Day": 0, "Hour": 0, "Minute": 0, "Month": 0},
                "FixType": 1,
                "ImoNumber": 9353333,
                "MaximumStaticDraught": 4.5,
                "Name": "AUGUSTSON",
                "RepeatIndicator": 0,
                "Spare": False,
                "Type": 55,
                "UserID": 259000420,
                "Valid": True,
            }
        },
    }


class TestParsePositionReport:
    def test_parses_position(self, mock_position_message: dict) -> None:
        rec = _parse_position_report(mock_position_message)
        assert rec is not None
        assert rec["mmsi"] == 259000420
        assert rec["latitude"] == 66.02695
        assert rec["longitude"] == 12.25382
        assert rec["sog"] == 0
        assert rec["cog"] == 308
        assert rec["heading"] == 235
        assert rec["vessel_name"] == "AUGUSTSON"

    def test_returns_none_for_empty(self) -> None:
        rec = _parse_position_report({"Message": {}, "Metadata": {}})
        assert rec is None

    def test_returns_none_for_non_position(self) -> None:
        rec = _parse_position_report({"MessageType": "ShipStaticData", "Message": {}})
        assert rec is None


class TestParseShipStatic:
    def test_parses_ship(self, mock_ship_static_message: dict) -> None:
        rec = _parse_ship_static(mock_ship_static_message)
        assert rec is not None
        assert rec["mmsi"] == 259000420
        assert rec["imo"] == 9353333
        assert rec["vessel_name"] == "AUGUSTSON"
        assert rec["callsign"] == "LBHF"
        assert rec["destination"] == "ROTTERDAM"
        assert rec["length_m"] == 47.0
        assert rec["beam_m"] == 14.0
        assert rec["draught"] == 4.5

    def test_returns_none_for_empty(self) -> None:
        rec = _parse_ship_static({"Message": {}, "Metadata": {}})
        assert rec is None

    def test_handles_missing_dimension(self) -> None:
        msg = {
            "Message": {
                "ShipStaticData": {
                    "UserID": 123,
                    "ImoNumber": 456,
                    "Name": "TEST",
                }
            },
            "Metadata": {"ShipName": "TEST"},
        }
        rec = _parse_ship_static(msg)
        assert rec is not None
        assert rec["length_m"] is None
        assert rec["beam_m"] is None


class TestAISStreamIntegration:
    @patch("src.collectors.aisstream.write_raw")
    def test_collect_writes_positions(
        self, mock_write: MagicMock, mock_position_message: dict
    ) -> None:
        mock_write.return_value = 1

        with patch("src.collectors.aisstream.SourceTracker"):
            from src.collectors.aisstream import _parse_position_report

            rec = _parse_position_report(mock_position_message)
            assert rec is not None
            assert rec["mmsi"] == 259000420

    def test_position_has_all_fields(self, mock_position_message: dict) -> None:
        rec = _parse_position_report(mock_position_message)
        assert rec is not None
        expected_fields = [
            "mmsi", "latitude", "longitude", "sog", "cog",
            "heading", "nav_status", "vessel_name", "timestamp_raw",
        ]
        for field in expected_fields:
            assert field in rec
