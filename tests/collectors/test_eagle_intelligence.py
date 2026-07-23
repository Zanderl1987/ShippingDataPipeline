from __future__ import annotations

from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.collectors.eagle_intelligence import (
    _parse_chokepoint_status,
    _parse_hormuz_status,
    get_chokepoint_status,
    get_hormuz_status,
)


@pytest.fixture
def mock_chokepoint_response() -> dict:
    return {
        "source": "Eagle Intelligence",
        "url": "https://eagleintelmari.com",
        "count": 6,
        "chokepoints": [
            {
                "chokepoint": "strait-of-hormuz",
                "name": "Strait of Hormuz",
                "status": "SEVERE",
                "signalsLast24h": 82,
                "highAlertsLast24h": 29,
                "signalsLast7d": 674,
                "latestHighHeadline": "Growing Red Sea crisis risks",
                "lastUpdated": "2026-07-23T18:39:28Z",
                "pageUrl": "https://eagleintelmari.com/chokepoint/strait-of-hormuz",
                "crisisDay": 146,
                "situationUrl": "https://eagleintelmari.com/situation/strait-of-hormuz",
            },
            {
                "chokepoint": "suez-canal",
                "name": "Suez Canal",
                "status": "MONITORING",
                "signalsLast24h": 0,
                "highAlertsLast24h": 0,
                "signalsLast7d": 13,
                "latestHighHeadline": None,
                "lastUpdated": "2026-07-22T17:42:35Z",
                "pageUrl": "https://eagleintelmari.com/chokepoint/suez-canal",
                "crisisDay": None,
                "situationUrl": None,
            },
            {
                "chokepoint": "bab-el-mandeb",
                "name": "Bab el-Mandeb Strait",
                "status": "ELEVATED",
                "signalsLast24h": 39,
                "highAlertsLast24h": 25,
                "signalsLast7d": 211,
                "latestHighHeadline": "Growing Red Sea crisis risks",
                "lastUpdated": "2026-07-23T18:39:28Z",
                "pageUrl": "https://eagleintelmari.com/chokepoint/bab-el-mandeb",
                "crisisDay": None,
                "situationUrl": None,
            },
            {
                "chokepoint": "panama-canal",
                "name": "Panama Canal",
                "status": "MONITORING",
                "signalsLast24h": 3,
                "highAlertsLast24h": 0,
                "signalsLast7d": 13,
                "latestHighHeadline": None,
                "lastUpdated": "2026-07-23T08:46:35Z",
                "pageUrl": "https://eagleintelmari.com/chokepoint/panama-canal",
                "crisisDay": None,
                "situationUrl": None,
            },
            {
                "chokepoint": "strait-of-malacca",
                "name": "Strait of Malacca",
                "status": "MONITORING",
                "signalsLast24h": 0,
                "highAlertsLast24h": 0,
                "signalsLast7d": 2,
                "latestHighHeadline": "Piracy Incidents Rise",
                "lastUpdated": "2026-07-21T10:00:41Z",
                "pageUrl": "https://eagleintelmari.com/chokepoint/strait-of-malacca",
                "crisisDay": None,
                "situationUrl": None,
            },
            {
                "chokepoint": "bosphorus",
                "name": "Bosphorus / Turkish Straits",
                "status": "ELEVATED",
                "signalsLast24h": 8,
                "highAlertsLast24h": 7,
                "signalsLast7d": 56,
                "latestHighHeadline": "Bosphorus disruption",
                "lastUpdated": "2026-07-23T12:00:00Z",
                "pageUrl": "https://eagleintelmari.com/chokepoint/bosphorus",
                "crisisDay": None,
                "situationUrl": None,
            },
        ],
    }


@pytest.fixture
def mock_hormuz_response() -> dict:
    return {
        "situation": "strait-of-hormuz",
        "name": "Strait of Hormuz Crisis",
        "status": "SEVERE",
        "statusNote": "Severe operational restrictions since 28 February 2026",
        "crisisDay": 146,
        "signalsLast24h": 82,
        "highAlertsLast24h": 29,
        "signalsLast7d": 674,
        "latestHighHeadline": "Growing Red Sea crisis risks",
        "lastUpdated": "2026-07-23T18:39:28Z",
        "situationUrl": "https://eagleintelmari.com/situation/strait-of-hormuz",
        "attribution": "Eagle Intelligence",
    }


class TestParseChokepointStatus:
    def test_parses_all_chokepoints(self, mock_chokepoint_response: dict) -> None:
        df = _parse_chokepoint_status(mock_chokepoint_response)
        assert df.height == 6
        assert "chokepoint_id" in df.columns
        assert "status" in df.columns
        assert "source" in df.columns
        assert "partition_date" in df.columns

    def test_chokepoint_ids_correct(self, mock_chokepoint_response: dict) -> None:
        df = _parse_chokepoint_status(mock_chokepoint_response)
        ids = df["chokepoint_id"].to_list()
        assert "strait-of-hormuz" in ids
        assert "suez-canal" in ids
        assert "bab-el-mandeb" in ids
        assert "panama-canal" in ids
        assert "strait-of-malacca" in ids
        assert "bosphorus" in ids

    def test_signals_counts(self, mock_chokepoint_response: dict) -> None:
        df = _parse_chokepoint_status(mock_chokepoint_response)
        hormuz = df.filter(pl.col("chokepoint_id") == "strait-of-hormuz")
        assert hormuz["signals_last_24h"][0] == 82
        assert hormuz["high_alerts_last_24h"][0] == 29
        assert hormuz["signals_last_7d"][0] == 674

    def test_status_values(self, mock_chokepoint_response: dict) -> None:
        df = _parse_chokepoint_status(mock_chokepoint_response)
        statuses = df["status"].to_list()
        assert "SEVERE" in statuses
        assert "MONITORING" in statuses
        assert "ELEVATED" in statuses

    def test_crisis_day(self, mock_chokepoint_response: dict) -> None:
        df = _parse_chokepoint_status(mock_chokepoint_response)
        hormuz = df.filter(pl.col("chokepoint_id") == "strait-of-hormuz")
        assert hormuz["crisis_day"][0] == 146

    def test_empty_response(self) -> None:
        df = _parse_chokepoint_status({"chokepoints": []})
        assert df.height == 0

    def test_source_and_partition_date(self, mock_chokepoint_response: dict) -> None:
        df = _parse_chokepoint_status(mock_chokepoint_response)
        assert df["source"][0] == "eagle_intelligence"
        assert df["partition_date"][0] is not None


class TestParseHormuzStatus:
    def test_parses_hormuz(self, mock_hormuz_response: dict) -> None:
        df = _parse_hormuz_status(mock_hormuz_response)
        assert df.height == 1
        assert df["status"][0] == "SEVERE"
        assert df["crisis_day"][0] == 146
        assert df["chokepoint_id"][0] == "strait-of-hormuz"

    def test_empty_response(self) -> None:
        df = _parse_hormuz_status({})
        assert df.height == 0

    def test_source_column(self, mock_hormuz_response: dict) -> None:
        df = _parse_hormuz_status(mock_hormuz_response)
        assert df["source"][0] == "eagle_intelligence"


class TestGetChokepointStatus:
    @patch("src.collectors.eagle_intelligence.requests.get")
    def test_returns_json(self, mock_get: MagicMock, mock_chokepoint_response: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_chokepoint_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_chokepoint_status()
        assert result["count"] == 6
        assert len(result["chokepoints"]) == 6
        mock_get.assert_called_once_with(
            "https://eagleintelmari.com/api/chokepoint-status", timeout=15
        )

    @patch("src.collectors.eagle_intelligence.requests.get")
    def test_raises_on_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("Connection failed")
        with pytest.raises(Exception, match="Connection failed"):
            get_chokepoint_status()


class TestGetHormuzStatus:
    @patch("src.collectors.eagle_intelligence.requests.get")
    def test_returns_json(self, mock_get: MagicMock, mock_hormuz_response: dict) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = mock_hormuz_response
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_hormuz_status()
        assert result["status"] == "SEVERE"
        mock_get.assert_called_once_with(
            "https://eagleintelmari.com/api/hormuz-status", timeout=15
        )
