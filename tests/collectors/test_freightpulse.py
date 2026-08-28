from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.collectors.freightpulse import (
    _parse_ports,
    _safe_float,
    _safe_int,
    collect_port_congestion,
    fetch_port_congestion,
)

SAMPLE_RESPONSE = {
    "success": True,
    "data": {
        "timestamp": "2026-08-26T09:24:24.058Z",
        "source": "Port Authorities + AIS Data",
        "total_ports": 2,
        "data": {
            "ports": [
                {
                    "port": "Los Angeles",
                    "port_code": "USLAX",
                    "country": "US",
                    "region": "North America",
                    "lat": 33.74,
                    "lon": -118.27,
                    "capacity_teu": 9500000,
                    "congestion_index": 45,
                    "congestion_level": "moderate",
                    "vessels_at_anchor": 8,
                    "vessels_at_berth": 38,
                    "avg_wait_time_hours": 18,
                    "avg_berth_time_hours": 59,
                    "container_dwell_days": 3,
                    "trend": "stable",
                    "change_week": 0,
                    "updated_at": "2026-08-26T09:24:24.058Z",
                },
                {
                    "port": "Singapore",
                    "port_code": "SGSIN",
                    "country": "SG",
                    "region": "Asia",
                    "lat": 1.29,
                    "lon": 103.85,
                    "capacity_teu": 38000000,
                    "congestion_index": 49,
                    "congestion_level": "moderate",
                    "vessels_at_anchor": 37,
                    "vessels_at_berth": 128,
                    "avg_wait_time_hours": 20,
                    "avg_berth_time_hours": 61,
                    "container_dwell_days": 3.1,
                    "trend": "stable",
                    "change_week": 5,
                    "updated_at": "2026-08-26T09:24:24.058Z",
                },
            ],
            "global_summary": {
                "avg_congestion_index": 48,
                "ports_high_congestion": 0,
                "ports_moderate": 98,
                "ports_low": 16,
            },
        },
    },
}


class TestParsePorts:
    def test_basic_parse(self) -> None:
        df = _parse_ports(SAMPLE_RESPONSE)
        assert df.height == 2
        assert "USLAX" in df["port_code"].to_list()
        assert "SGSIN" in df["port_code"].to_list()

    def test_columns(self) -> None:
        df = _parse_ports(SAMPLE_RESPONSE)
        expected_cols = {
            "snapshot_date", "port_code", "port_name", "country", "region",
            "latitude", "longitude", "capacity_teu", "congestion_index",
            "congestion_level", "vessels_at_anchor", "vessels_at_berth",
            "avg_wait_time_hours", "avg_berth_time_hours", "container_dwell_days",
            "trend", "change_week", "source", "partition_date",
        }
        assert expected_cols.issubset(set(df.columns))

    def test_snapshot_date_from_timestamp(self) -> None:
        df = _parse_ports(SAMPLE_RESPONSE)
        dates = df["snapshot_date"].unique().to_list()
        assert len(dates) == 1
        assert dates[0] == date(2026, 8, 26)

    def test_numeric_fields(self) -> None:
        df = _parse_ports(SAMPLE_RESPONSE)
        lax = df.filter(pl.col("port_code") == "USLAX").to_dicts()[0]
        assert lax["latitude"] == pytest.approx(33.74)
        assert lax["congestion_index"] == pytest.approx(45.0)
        assert lax["vessels_at_anchor"] == 8
        assert lax["container_dwell_days"] == pytest.approx(3.0)

    def test_empty_response(self) -> None:
        df = _parse_ports({"data": {"data": {}}})
        assert df.height == 0

    def test_missing_ports_array(self) -> None:
        df = _parse_ports({"data": {"data": {"ports": "not_a_list"}}})
        assert df.height == 0

    def test_skips_port_without_code(self) -> None:
        resp = {
            "data": {
                "data": {
                    "ports": [
                        {"port": "No Code"},
                        {
                            "port": "LAX",
                            "port_code": "USLAX",
                            "country": "US",
                        },
                    ]
                }
            }
        }
        df = _parse_ports(resp)
        assert df.height == 1
        assert df["port_code"][0] == "USLAX"

    def test_source_and_partition_columns(self) -> None:
        df = _parse_ports(SAMPLE_RESPONSE)
        assert (df["source"] == "freightpulse").all()
        assert df["partition_date"].dtype == pl.Date


# ── Unit tests for helper functions ──────────────────────────────────────────


class TestSafeFloat:
    def test_int(self) -> None:
        assert _safe_float(42) == 42.0

    def test_float(self) -> None:
        assert _safe_float(3.14) == pytest.approx(3.14)

    def test_string(self) -> None:
        assert _safe_float("7.5") == pytest.approx(7.5)

    def test_none(self) -> None:
        assert _safe_float(None) is None

    def test_invalid(self) -> None:
        assert _safe_float("abc") is None


class TestSafeInt:
    def test_int(self) -> None:
        assert _safe_int(42) == 42

    def test_float(self) -> None:
        assert _safe_int(5.9) == 5

    def test_none(self) -> None:
        assert _safe_int(None) is None

    def test_invalid(self) -> None:
        assert _safe_int("abc") is None


# ── Integration tests with mocked HTTP ──────────────────────────────────────


class TestCollectPortCongestion:
    @patch("src.collectors.freightpulse.SourceTracker")
    @patch("src.collectors.freightpulse.write_raw", return_value=2)
    @patch("src.collectors.freightpulse.fetch_port_congestion", return_value=SAMPLE_RESPONSE)
    def test_collect_writes_records(
        self, mock_fetch: MagicMock, mock_write: MagicMock, mock_tracker: MagicMock
    ) -> None:
        count = collect_port_congestion(tracker=None)
        assert count == 2
        mock_fetch.assert_called_once()
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][0] == "freightpulse"
        assert call_args[0][1].height == 2
        assert call_args[1]["table_name"] == "port_congestion"

    @patch("src.collectors.freightpulse.SourceTracker")
    @patch("src.collectors.freightpulse.write_raw", return_value=0)
    @patch(
        "src.collectors.freightpulse.fetch_port_congestion",
        return_value={"data": {"data": {}}},
    )
    def test_collect_empty_returns_zero(
        self, mock_fetch: MagicMock, mock_write: MagicMock, mock_tracker: MagicMock
    ) -> None:
        count = collect_port_congestion(tracker=None)
        assert count == 0
        mock_write.assert_not_called()

    @patch("src.collectors.freightpulse.get_with_retry")
    def test_fetch_returns_json(self, mock_get: MagicMock) -> None:
        mock_get.return_value = MagicMock(json=lambda: SAMPLE_RESPONSE)
        data = fetch_port_congestion()
        assert data["success"] is True
        assert data["data"]["total_ports"] == 2
