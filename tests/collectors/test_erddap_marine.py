"""Tests for the ERDDAP marine SST collector."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.collectors.erddap_marine import (
    ALL_LOCATIONS,
    BASE_URL,
    DATASET,
    VARIABLE,
    _build_query_url,
    _parse_sst_value,
    collect_erddap_marine,
)


class TestParseSstValue:
    def test_valid_csv(self):
        csv_text = (
            "time,latitude,longitude,analysed_sst\n"
            "2026-08-24T09:00:00Z,26.26,55.69,33.098\n"
        )
        assert _parse_sst_value(csv_text) == pytest.approx(33.098)

    def test_valid_csv_with_extra_columns(self):
        csv_text = (
            "time,latitude,longitude,analysed_sst,analysis_error\n"
            "2026-08-24T09:00:00Z,26.26,55.69,33.098,0.15\n"
        )
        assert _parse_sst_value(csv_text) == pytest.approx(33.098)

    def test_empty_response(self):
        assert _parse_sst_value("") is None

    def test_whitespace_response(self):
        assert _parse_sst_value("   \n  ") is None

    def test_no_data_rows(self):
        csv_text = "time,latitude,longitude,analysed_sst\n"
        assert _parse_sst_value(csv_text) is None

    def test_error_response_html(self):
        assert _parse_sst_value("<html>Error</html>") is None

    def test_missing_analysed_sst_column(self):
        csv_text = "time,latitude,longitude\n2026-08-24T09:00:00Z,26.26,55.69\n"
        assert _parse_sst_value(csv_text) is None

    def test_nan_value_skipped(self):
        csv_text = (
            "time,latitude,longitude,analysed_sst\n"
            "2026-08-24T09:00:00Z,26.26,55.69,\n"
        )
        assert _parse_sst_value(csv_text) is None


class TestBuildQueryUrl:
    def test_hormuz(self):
        url = _build_query_url(26.25, 56.0)
        assert DATASET in url
        assert VARIABLE in url
        assert "[last]" in url
        assert "26.2" in url
        assert "55.95" in url
        assert url.startswith(BASE_URL)

    def test_panama(self):
        url = _build_query_url(9.15, -79.8)
        assert "9.1" in url
        assert "9.2" in url
        assert "79.85" in url
        assert "79.75" in url

    def test_singapore(self):
        url = _build_query_url(1.26, 103.83)
        assert "1.21" in url
        assert "1.31" in url
        assert "103.78" in url
        assert "103.88" in url


class TestCollectErddapMarine:
    @patch("src.collectors.erddap_marine.write_raw")
    @patch("src.collectors.erddap_marine.get_with_retry")
    def test_collect_writes_records(self, mock_get, mock_write):
        csv_text = (
            "time,latitude,longitude,analysed_sst\n"
            "2026-08-24T09:00:00Z,26.26,55.69,33.098\n"
        )
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_get.return_value = mock_resp
        mock_write.return_value = len(ALL_LOCATIONS)

        count = collect_erddap_marine()
        assert count == len(ALL_LOCATIONS)
        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[1]["table_name"] == "marine_weather"

    @patch("src.collectors.erddap_marine.write_raw")
    @patch("src.collectors.erddap_marine.get_with_retry")
    def test_collect_returns_zero_on_all_failures(self, mock_get, mock_write):
        mock_get.side_effect = Exception("HTTP error")
        count = collect_erddap_marine()
        assert count == 0
        mock_write.assert_not_called()

    @patch("src.collectors.erddap_marine.write_raw")
    @patch("src.collectors.erddap_marine.get_with_retry")
    def test_collect_writes_dataframe_with_correct_columns(self, mock_get, mock_write):
        csv_text = (
            "time,latitude,longitude,analysed_sst\n"
            "2026-08-24T09:00:00Z,26.26,55.69,33.098\n"
        )
        mock_resp = MagicMock()
        mock_resp.text = csv_text
        mock_get.return_value = mock_resp
        mock_write.return_value = 1

        collect_erddap_marine()
        df = mock_write.call_args[0][1]
        assert set(df.columns) >= {
            "timestamp",
            "latitude",
            "longitude",
            "sea_surface_temperature",
            "source",
            "partition_date",
        }
