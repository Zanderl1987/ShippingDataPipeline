from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.collectors.freightpulse_disruptions import (
    _parse_alert,
    collect_disruptions,
)
from src.collectors.freightpulse_fuel import (
    _parse_fuel_prices,
    collect_fuel_prices,
)

# ── Fuel Prices ───────────────────────────────────────────────────────────────

SAMPLE_FUEL_PRICES_RESPONSE = {
    "success": True,
    "data": {
        "timestamp": "2026-08-26T09:38:20.291Z",
        "source": "EIA (Live)",
        "currency": "USD",
        "unit": "gallon",
        "data": {
            "diesel": {
                "national_average": 4.48,
                "change_week": -0.023,
                "change_percent": -0.6,
                "regions": {
                    "east_coast": 3.912,
                    "midwest": 3.756,
                    "gulf_coast": 3.623,
                    "rocky_mountain": 3.891,
                    "west_coast": 4.456,
                    "california": 4.892,
                },
                "updated_at": "2026-08-26T09:38:20.291Z",
            },
            "gasoline": {
                "national_average": 4.48,
                "change_week": 0.034,
                "change_percent": 1.1,
                "grades": {
                    "regular": 3.156,
                    "midgrade": 3.567,
                    "premium": 3.923,
                },
                "updated_at": "2026-08-26T09:38:20.291Z",
            },
            "bunker_fuel": {
                "rotterdam": 512.5,
                "singapore": 534.25,
                "houston": 498.75,
                "unit": "metric_ton",
                "updated_at": "2026-08-26T09:38:20.291Z",
            },
        },
        "historical": {
            "diesel_30d_avg": 3.82,
            "diesel_90d_avg": 3.75,
            "diesel_yoy_change": -8.2,
        },
    },
}


class TestFuelPricesParsing:
    def test_parse_fuel_prices_all_fields(self):
        data = SAMPLE_FUEL_PRICES_RESPONSE["data"]
        record = _parse_fuel_prices(data)
        assert record["diesel_national_avg"] == 4.48
        assert record["diesel_east_coast"] == 3.912
        assert record["bunker_rotterdam"] == 512.5
        assert record["bunker_singapore"] == 534.25
        assert record["gasoline_regular"] == 3.156
        assert record["diesel_yoy_change"] == -8.2

    def test_parse_fuel_prices_missing_bunker(self):
        data = {
            "data": {
                "diesel": {"national_average": 3.5},
                "gasoline": {"grades": {}},
                "bunker_fuel": {},
            },
        }
        record = _parse_fuel_prices(data)
        assert record["diesel_national_avg"] == 3.5
        assert record["bunker_rotterdam"] is None

    def test_parse_fuel_prices_all_none(self):
        record = _parse_fuel_prices({})
        assert all(v is None for v in record.values())


class TestFuelPricesCollect:
    @patch("src.collectors.freightpulse_fuel.get_with_retry")
    def test_collect_fuel_prices(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_FUEL_PRICES_RESPONSE
        mock_get.return_value = mock_resp

        with patch(
            "src.collectors.freightpulse_fuel.write_raw", return_value=1
        ) as mock_write:
            count = collect_fuel_prices()
            assert count == 1
            mock_write.assert_called_once()
            call_args = mock_write.call_args
            assert call_args[0][0] == "freightpulse_fuel"
            assert call_args[1]["table_name"] == "fuel_prices"

    @patch("src.collectors.freightpulse_fuel.get_with_retry")
    def test_collect_fuel_prices_success_false(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": False}
        mock_get.return_value = mock_resp

        with patch(
            "src.collectors.freightpulse_fuel.write_raw", return_value=0
        ):
            count = collect_fuel_prices()
            assert count == 0


# ── Disruptions ───────────────────────────────────────────────────────────────

SAMPLE_DISRUPTIONS_RESPONSE = {
    "success": True,
    "data": {
        "timestamp": "2026-08-26T09:38:23.139Z",
        "source": "FreightPulse Intelligence",
        "active_alerts": 4,
        "alerts": [
            {
                "id": "DISR-2026-0315-001",
                "type": "geopolitical",
                "severity": "high",
                "title": "Red Sea Shipping Disruption",
                "description": "Houthi attacks continue to affect vessels.",
                "affected_regions": [
                    "Middle East",
                    "Asia-Europe Trade",
                ],
                "affected_routes": ["SHAROT", "SINSOU", "HKGROT"],
                "impact": {
                    "transit_delay_days": 10,
                    "rate_increase_percent": 15,
                    "capacity_reduction_percent": 8,
                },
                "started_at": "2023-11-19T00:00:00Z",
                "status": "ongoing",
                "updated_at": "2026-08-26T09:38:23.139Z",
            },
        ],
        "resolved_recently": [
            {
                "id": "DISR-2026-0228-001",
                "type": "weather",
                "title": "US West Coast Winter Storms",
                "resolved_at": "2026-03-05T00:00:00Z",
                "duration_days": 5,
            },
        ],
        "risk_forecast": {
            "next_7_days": "moderate",
            "factors": ["Red Sea situation remains volatile"],
        },
    },
}


class TestDisruptionsParsing:
    def test_parse_alert_full(self):
        alert = SAMPLE_DISRUPTIONS_RESPONSE["data"]["alerts"][0]
        rec = _parse_alert(alert)
        assert rec["disruption_id"] == "DISR-2026-0315-001"
        assert rec["disruption_type"] == "geopolitical"
        assert rec["severity"] == "high"
        assert rec["transit_delay_days"] == 10
        assert rec["rate_increase_pct"] == 15.0
        assert rec["capacity_reduction_pct"] == 8.0
        assert rec["status"] == "ongoing"

    def test_parse_alert_affected_routes_json(self):
        alert = SAMPLE_DISRUPTIONS_RESPONSE["data"]["alerts"][0]
        rec = _parse_alert(alert)
        import json

        routes = json.loads(rec["affected_routes"])
        assert routes == ["SHAROT", "SINSOU", "HKGROT"]

    def test_parse_alert_minimal(self):
        rec = _parse_alert({})
        assert rec["disruption_id"] == ""
        assert rec["transit_delay_days"] is None

    def test_parse_resolved_alert_gets_resolved_status(self):
        resolved = SAMPLE_DISRUPTIONS_RESPONSE["data"]["resolved_recently"][0]
        rec = _parse_alert(resolved)
        assert rec["disruption_id"] == "DISR-2026-0228-001"
        assert rec["title"] == "US West Coast Winter Storms"


class TestDisruptionsCollect:
    @patch("src.collectors.freightpulse_disruptions.get_with_retry")
    def test_collect_disruptions(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_DISRUPTIONS_RESPONSE
        mock_get.return_value = mock_resp

        with patch(
            "src.collectors.freightpulse_disruptions.write_raw",
            return_value=2,
        ) as mock_write:
            count = collect_disruptions()
            assert count == 2
            mock_write.assert_called_once()
            call_args = mock_write.call_args
            assert call_args[0][0] == "freightpulse_disruptions"
            assert call_args[0][1].height == 2
            assert (
                call_args[1]["table_name"] == "supply_chain_disruptions"
            )

    @patch("src.collectors.freightpulse_disruptions.get_with_retry")
    def test_collect_disruptions_success_false(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"success": False}
        mock_get.return_value = mock_resp

        with patch(
            "src.collectors.freightpulse_disruptions.write_raw",
            return_value=0,
        ):
            count = collect_disruptions()
            assert count == 0

    @patch("src.collectors.freightpulse_disruptions.get_with_retry")
    def test_collect_disruptions_resolved_marked(self, mock_get):
        mock_resp = MagicMock()
        mock_resp.json.return_value = SAMPLE_DISRUPTIONS_RESPONSE
        mock_get.return_value = mock_resp

        with patch(
            "src.collectors.freightpulse_disruptions.write_raw"
        ) as mock_write:
            mock_write.return_value = 2
            collect_disruptions()
            df = mock_write.call_args[0][1]
            resolved_rows = df.filter(df["status"] == "resolved")
            assert resolved_rows.height == 1
            assert (
                resolved_rows["disruption_id"][0] == "DISR-2026-0228-001"
            )
