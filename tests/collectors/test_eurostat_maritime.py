from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.eurostat_maritime import flatten_jsonstat, parse_eurostat_maritime_records

SAMPLE_PAYLOAD = {
    "dimension": {
        "time": {
            "category": {
                "index": ["1997", "1998"],
                "label": {"1997": "1997", "1998": "1998"},
            }
        },
        "rep_mar": {
            "category": {
                "index": ["BE", "NL"],
                "label": {"BE": "Belgium", "NL": "Netherlands"},
            }
        },
        "direct": {
            "category": {
                "index": ["IN", "OUT"],
                "label": {"IN": "Inwards", "OUT": "Outwards"},
            }
        },
    },
    "value": [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, None, 160.0],
}


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


def test_flatten_jsonstat() -> None:
    records = flatten_jsonstat(SAMPLE_PAYLOAD)
    assert len(records) == 7
    by_key = {(r["time_period"], r["geo"], r["direction"]): r for r in records}
    assert by_key[("1997", "BE", "IN")]["value"] == 100.0
    assert by_key[("1997", "BE", "OUT")]["value"] == 110.0
    assert by_key[("1997", "NL", "IN")]["value"] == 120.0
    assert by_key[("1998", "NL", "OUT")]["value"] == 160.0
    assert by_key[("1997", "BE", "IN")]["geo_label"] == "Belgium"
    assert by_key[("1997", "BE", "IN")]["direction_label"] == "Inwards"


def test_flatten_jsonstat_empty() -> None:
    assert flatten_jsonstat({}) == []


def test_parse_eurostat_maritime_records() -> None:
    df = parse_eurostat_maritime_records(SAMPLE_PAYLOAD)
    assert df.height == 7
    assert "time_period" in df.columns
    assert "geo" in df.columns
    assert "geo_label" in df.columns
    assert "direction" in df.columns
    assert "unit" in df.columns
    assert "value" in df.columns
    assert "source" in df.columns
    assert set(df["source"].unique().to_list()) == {"eurostat_maritime"}
    assert "freq" not in df.columns


def test_parse_eurostat_maritime_records_empty() -> None:
    assert parse_eurostat_maritime_records({}).height == 0


@patch("src.collectors.eurostat_maritime.write_raw")
@patch("src.collectors.eurostat_maritime.fetch_eurostat_maritime_data")
def test_collect_eurostat_maritime(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = SAMPLE_PAYLOAD
    mock_write.return_value = 7

    from src.collectors.eurostat_maritime import collect_eurostat_maritime_data

    count = collect_eurostat_maritime_data()
    assert count == 7
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()
