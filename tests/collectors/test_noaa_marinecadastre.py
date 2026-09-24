from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch

import duckdb
import polars as pl
import pytest

from src.collectors.noaa_marinecadastre import (
    INDEX_URL,
    TABLE,
    _months_to_fetch,
    _parse_index,
    collect_vessel_tracks,
)
from src.storage.writer import get_db_path, init_db

INDEX_HTML = """<html><body>
<a href="../">../</a>
<a href="ais-track-2024-01.parquet">ais-track-2024-01.parquet</a>
<a href="ais-track-2025-12.parquet">ais-track-2025-12.parquet</a>
<a href="ais-track-2024-02.parquet">ais-track-2024-02.parquet</a>
<a href="readme.md">readme.md</a>
</body></html>"""


def _tracks(n: int = 2, day: int = 1) -> pl.DataFrame:
    """Rows shaped like the NOAA file after the geometry column is dropped."""
    return pl.DataFrame(
        {
            "mmsi": list(range(100, 100 + n)),
            "vessel_name": ["A"] * n,
            "imo": ["IMO1"] * n,
            "call_sign": ["C"] * n,
            "vessel_type": [70] * n,
            "vessel_type_name": ["Cargo"] * n,
            "status": [0] * n,
            "length": [200.0] * n,
            "width": [30] * n,
            "draft": [9.3] * n,
            "cargo": [70] * n,
            "transceiver": ["A"] * n,
            "duration_minutes": [60] * n,
            "start_time": [datetime(2025, 12, day, 1)] * n,
            "end_time": [datetime(2025, 12, day, 2)] * n,
        }
    )


class TestParseIndex:
    def test_months_sorted_and_non_track_links_ignored(self) -> None:
        assert _parse_index(INDEX_HTML) == [
            date(2024, 1, 1),
            date(2024, 2, 1),
            date(2025, 12, 1),
        ]

    def test_empty(self) -> None:
        assert _parse_index("<html></html>") == []


class TestMonthsToFetch:
    AVAILABLE = [date(2025, m, 1) for m in (9, 10, 11, 12)]

    def test_bulk_fetches_every_missing_month(self) -> None:
        assert _months_to_fetch(self.AVAILABLE, {date(2025, 10, 1)}, bulk=True) == [
            date(2025, 9, 1),
            date(2025, 11, 1),
            date(2025, 12, 1),
        ]

    def test_local_empty_table_fetches_only_the_newest(self) -> None:
        # No bulk data on a dev machine: one month, not two years.
        assert _months_to_fetch(self.AVAILABLE, set(), bulk=False) == [date(2025, 12, 1)]

    def test_local_fetches_new_months_but_never_backfills(self) -> None:
        assert _months_to_fetch(self.AVAILABLE, {date(2025, 10, 1)}, bulk=False) == [
            date(2025, 11, 1),
            date(2025, 12, 1),
        ]

    def test_nothing_new(self) -> None:
        assert _months_to_fetch(self.AVAILABLE, set(self.AVAILABLE), bulk=True) == []


@pytest.fixture
def db() -> None:
    # tests/conftest.py already points storage at a tmp dir and runs init_db().
    init_db()


def _stored() -> list[tuple]:
    conn = duckdb.connect(str(get_db_path()), read_only=True)
    try:
        return conn.execute(
            f"SELECT track_month, mmsi, start_time, source FROM {TABLE} ORDER BY mmsi"
        ).fetchall()
    finally:
        conn.close()


class TestCollectVesselTracks:
    @patch("src.collectors.noaa_marinecadastre._read_month")
    @patch("src.collectors.noaa_marinecadastre._fetch_index")
    def test_writes_tracks_with_month_and_is_idempotent(
        self, mock_index, mock_read, db
    ) -> None:
        mock_index.return_value = [date(2025, 12, 1)]
        mock_read.return_value = _tracks()

        assert collect_vessel_tracks(bulk_backfill=True) == 2
        rows = _stored()
        assert rows == [
            (date(2025, 12, 1), 100, datetime(2025, 12, 1, 1), "noaa_marinecadastre"),
            (date(2025, 12, 1), 101, datetime(2025, 12, 1, 1), "noaa_marinecadastre"),
        ]
        mock_read.assert_called_once_with(date(2025, 12, 1))

        # The month is now stored, so a second run fetches nothing.
        mock_read.reset_mock()
        assert collect_vessel_tracks(bulk_backfill=True) == 0
        mock_read.assert_not_called()
        assert len(_stored()) == 2

    @patch("src.collectors.noaa_marinecadastre._read_month")
    @patch("src.collectors.noaa_marinecadastre._fetch_index")
    def test_records_tracker_row_under_registered_name(
        self, mock_index, mock_read, db
    ) -> None:
        from src.storage.tracker import SourceTracker

        mock_index.return_value = [date(2025, 12, 1)]
        mock_read.return_value = _tracks(3)
        tracker = SourceTracker()
        collect_vessel_tracks(tracker=tracker, bulk_backfill=True)
        last = tracker.get_last_collection("noaa_marinecadastre")
        assert last is not None
        assert (last["rows_fetched"], last["rows_written"]) == (3, 3)

    @patch("src.collectors.noaa_marinecadastre._fetch_index")
    def test_empty_index_raises(self, mock_index, db) -> None:
        # An index with no track files means the layout moved again; that
        # must fail loudly, not report "success, 0 rows" for months.
        mock_index.return_value = []
        with pytest.raises(RuntimeError, match=INDEX_URL):
            collect_vessel_tracks(bulk_backfill=True)
