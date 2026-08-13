from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.noaa_storms import (
    _damage_to_millions,
    _parse_ts,
    _to_int,
    parse_storm_csv,
)

STORM_CSV = (
    "EVENT_ID,EVENT_TYPE,BEGIN_DATE_TIME,END_DATE_TIME,STATE,CZ_NAME,"
    "BEGIN_LAT,BEGIN_LON,INJURIES_DIRECT,INJURIES_INDIRECT,DEATHS_DIRECT,"
    "DEATHS_INDIRECT,DAMAGE_PROPERTY,DAMAGE_CROPS,EPISODE_ID,EVENT_NARRATIVE\n"
    "1,Hail,14-APR-26 19:10:00,14-APR-26 20:00:00,TEXAS,DALLAS,42.44,-88.01,"
    "0,0,0,0,50.00K,5.00M,1,\"Line one\nLine two still in the narrative\"\n"
    '2,Thunderstorm Wind,02-MAR-95 05:00:00,,KANSAS,RILEY,39.10,-96.60,1,0,0,'
    '0,0.0,0.0,2,""\n'
)


@pytest.fixture
def mock_settings(tmp_path: Path) -> None:
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    yield
    settings.data_dir = old_data
    settings.storage_dir = old_storage


def test_damage_to_millions() -> None:
    assert _damage_to_millions("50.00K") == 0.05
    assert _damage_to_millions("5.00M") == 5.0
    assert _damage_to_millions("1.0B") == 1000.0
    assert _damage_to_millions("123") == pytest.approx(0.000123)
    assert _damage_to_millions("") is None
    assert _damage_to_millions(None) is None
    assert _damage_to_millions("n/a") is None


def test_parse_ts() -> None:
    assert _parse_ts("14-APR-26 19:10:00") == datetime(2026, 4, 14, 19, 10, 0)
    assert _parse_ts("02-MAR-95 05:00:00") == datetime(1995, 3, 2, 5, 0, 0)
    assert _parse_ts("") is None
    assert _parse_ts(None) is None
    assert _parse_ts("garbage") is None


def test_to_int() -> None:
    assert _to_int("3") == 3
    assert _to_int("0") == 0
    assert _to_int("") is None
    assert _to_int(None) is None


def test_parse_storm_csv_with_embedded_newlines() -> None:
    df = parse_storm_csv(STORM_CSV)
    assert df.height == 2
    assert set(df["source"].unique().to_list()) == {"noaa_storms"}
    assert df[0, "event_id"] == "1"
    assert df[0, "event_type"] == "Hail"
    assert df[0, "state"] == "TEXAS"
    assert df[0, "begin_date"] == datetime(2026, 4, 14, 19, 10, 0)
    assert df[0, "damage_property_millions"] == 0.05
    assert df[0, "damage_crops_millions"] == 5.0
    assert df[1, "begin_date"] == datetime(1995, 3, 2, 5, 0, 0)
    assert df[1, "injuries_direct"] == 1


def test_parse_storm_csv_empty() -> None:
    assert parse_storm_csv("").height == 0
    assert parse_storm_csv(None).height == 0


@patch("src.collectors.noaa_storms.write_raw")
@patch("src.collectors.noaa_storms.fetch_storm_csv")
@patch("src.collectors.noaa_storms.list_storm_files")
def test_collect_noaa_storms(
    mock_list: MagicMock,
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_list.return_value = {2025: "http://example/d2025_c1.csv.gz", 2026: "http://example/d2026_c1.csv.gz"}
    mock_fetch.return_value = STORM_CSV
    mock_write.return_value = 2

    from src.collectors.noaa_storms import collect_noaa_storms_data

    count = collect_noaa_storms_data()
    assert count == 2
    assert mock_fetch.call_count == 2
    mock_write.assert_called_once()
