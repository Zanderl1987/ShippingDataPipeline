"""Tests for the ERDDAP marine SST collector."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch

import pytest
import requests

from src.collectors.erddap_marine import (
    ALL_LOCATIONS,
    BASE_URL,
    DATASET,
    VARIABLE,
    _build_query_url,
    _parse_sst_rows,
    collect_erddap_marine,
)

# ERDDAP's .csv has a units row under the header.
HEADER = "time,latitude,longitude,analysed_sst\nUTC,degrees_north,degrees_east,degree_C\n"


class TestParseSstRows:
    def test_one_row_per_day(self):
        rows = _parse_sst_rows(
            HEADER
            + "2026-09-22T09:00:00Z,26.25,56.0,31.948\n"
            + "2026-09-23T09:00:00Z,26.25,56.0,31.871\n"
        )
        assert rows == [
            (datetime(2026, 9, 22, 9), pytest.approx(31.948)),
            (datetime(2026, 9, 23, 9), pytest.approx(31.871)),
        ]

    def test_extra_columns(self):
        rows = _parse_sst_rows(
            "time,latitude,longitude,analysed_sst,analysis_error\n"
            "2026-08-24T09:00:00Z,26.26,55.69,33.098,0.15\n"
        )
        assert rows == [(datetime(2026, 8, 24, 9), pytest.approx(33.098))]

    def test_nan_is_skipped(self):
        # MUR masks land, so a point on land returns the literal string NaN,
        # which float() would happily accept.
        rows = _parse_sst_rows(
            HEADER
            + "2026-09-22T09:00:00Z,31.2,121.5,NaN\n"
            + "2026-09-23T09:00:00Z,31.2,121.5,\n"
        )
        assert rows == []

    @pytest.mark.parametrize(
        "text",
        [
            "",
            "   \n  ",
            "time,latitude,longitude,analysed_sst\n",
            "<html>Error</html>",
            "time,latitude,longitude\n2026-08-24T09:00:00Z,26.26,55.69\n",
        ],
    )
    def test_no_usable_rows(self, text):
        assert _parse_sst_rows(text) == []


class TestBuildQueryUrl:
    def test_time_range_and_nearest_point(self):
        url = _build_query_url(26.25, 56.0, date(2026, 9, 17))
        assert url.startswith(f"{BASE_URL}/{DATASET}.csv?{VARIABLE}")
        # A time range, so a day missed by one run is picked up by the next,
        # and a single lat/lon, which ERDDAP snaps to the nearest grid cell.
        assert url.endswith("[(2026-09-17T00:00:00Z):1:(last)][(26.25)][(56.0)]")


class TestLocations:
    def test_no_name_collisions(self):
        # "Suez" was both a chokepoint and a port; the dict merge silently
        # dropped one of them.
        from src.collectors.erddap_marine import CHOKEPOINTS, PORTS

        assert len(ALL_LOCATIONS) == len(CHOKEPOINTS) + len(PORTS)

    def test_coordinates_are_distinct(self):
        coords = list(ALL_LOCATIONS.values())
        assert len(set(coords)) == len(coords)


def _resp(text: str) -> MagicMock:
    resp = MagicMock()
    resp.text = text
    return resp


class TestCollectErddapMarine:
    @patch("src.collectors.erddap_marine.write_raw")
    @patch("src.collectors.erddap_marine.get_with_retry")
    def test_writes_one_row_per_location_day(self, mock_get, mock_write):
        mock_get.return_value = _resp(
            HEADER
            + "2026-09-22T09:00:00Z,26.25,56.0,31.948\n"
            + "2026-09-23T09:00:00Z,26.25,56.0,31.871\n"
        )
        mock_write.return_value = 2 * len(ALL_LOCATIONS)

        assert collect_erddap_marine() == 2 * len(ALL_LOCATIONS)
        mock_write.assert_called_once()
        assert mock_write.call_args[1]["table_name"] == "marine_weather"
        df = mock_write.call_args[0][1]
        assert df.height == 2 * len(ALL_LOCATIONS)
        assert set(df.columns) >= {
            "timestamp",
            "latitude",
            "longitude",
            "sea_surface_temperature",
            "source",
            "partition_date",
        }

    @patch("src.collectors.erddap_marine.write_raw")
    @patch("src.collectors.erddap_marine.get_with_retry")
    def test_timestamp_is_the_observation_time(self, mock_get, mock_write):
        # Stamping the collection time made the same satellite day a new row
        # on every run; the observation time lets dedup collapse re-fetches.
        mock_get.return_value = _resp(HEADER + "2026-09-22T09:00:00Z,26.25,56.0,31.948\n")
        mock_write.return_value = len(ALL_LOCATIONS)

        collect_erddap_marine()
        df = mock_write.call_args[0][1]
        # Naive UTC, so the stored value doesn't depend on the host's zone.
        assert set(df["timestamp"].to_list()) == {datetime(2026, 9, 22, 9)}
        # Rows carry the configured location, not ERDDAP's snapped grid cell.
        assert set(zip(df["latitude"], df["longitude"], strict=True)) == set(
            ALL_LOCATIONS.values()
        )

    @patch("src.collectors.erddap_marine.write_raw")
    @patch("src.collectors.erddap_marine.get_with_retry")
    def test_returns_zero_on_all_failures(self, mock_get, mock_write):
        mock_get.side_effect = Exception("HTTP error")
        assert collect_erddap_marine() == 0
        mock_write.assert_not_called()

    @patch("src.collectors.erddap_marine.write_raw")
    @patch("src.collectors.erddap_marine.get_with_retry")
    def test_http_error_logs_response_body(self, mock_get, mock_write, caplog):
        # CI got bare 403s with an empty message; the body says why.
        resp = requests.Response()
        resp.status_code = 403
        resp._content = b"example 403 body"
        mock_get.side_effect = requests.HTTPError("403 Client Error", response=resp)

        collect_erddap_marine()
        assert "403" in caplog.text
        assert "example 403 body" in caplog.text
