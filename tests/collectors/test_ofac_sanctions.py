from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.ofac_sanctions import _clean, parse_sdn_csv


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


SDN_CSV = (
    "ent_num,sdn_name,sdn_type,program,title,call_sign,vessel_type,tonnage,"
    "grt,vessel_flag,vessel_owner,remarks\n"
    "1,SHADOW TANKER LLC,,,,,Tanker,50000,,,,SANCTIONED VESSEL\n"
    "2,IVAN IVANOV,individual,SDGT,,,,,,,,-0-\n"
)


def test_clean_treats_placeholder_as_missing() -> None:
    assert _clean("  abc  ") == "abc"
    assert _clean("-0-") is None
    assert _clean("") is None
    assert _clean(None) is None


def test_parse_sdn_csv() -> None:
    df = parse_sdn_csv(SDN_CSV)
    assert df.height == 2
    assert set(df["source"].unique().to_list()) == {"ofac_sdn"}
    assert df[0, "entity_id"] == "1"
    assert df[0, "name"] == "SHADOW TANKER LLC"
    assert df[0, "vessel_type"] == "Tanker"
    assert df[0, "country"] is None
    assert df[1, "entity_type"] == "individual"
    assert df[1, "remarks"] is None


def test_parse_sdn_csv_empty() -> None:
    assert parse_sdn_csv("") is None or parse_sdn_csv("").height == 0
    assert parse_sdn_csv(None).height == 0


@patch("src.collectors.ofac_sanctions.write_raw")
@patch("src.collectors.ofac_sanctions.fetch_sdn_data")
def test_collect_ofac_sanctions(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = SDN_CSV
    mock_write.return_value = 2

    from src.collectors.ofac_sanctions import collect_ofac_sanctions_data

    count = collect_ofac_sanctions_data()
    assert count == 2
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()
