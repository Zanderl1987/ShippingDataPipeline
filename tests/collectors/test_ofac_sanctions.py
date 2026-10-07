from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.ofac_sanctions import _clean, _extract_vessel_imo, parse_sdn_csv


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


VESSEL_ID = "Vessel Registration Identification"


def test_extract_vessel_imo() -> None:
    remarks = f"Secondary sanctions risk: see EO 13846; {VESSEL_ID} IMO 9113379; MMSI 668116259"
    assert _extract_vessel_imo(remarks) == "9113379"
    # OFAC sometimes puts two spaces before the number.
    assert _extract_vessel_imo(f"{VESSEL_ID} IMO  8730455; Linked To: X.") == "8730455"
    # A company's IMO number is not a vessel's.
    assert _extract_vessel_imo("Identification Number IMO 5422497; Linked To: Y.") is None
    # National registrations and unlabeled numbers are not IMO numbers.
    assert _extract_vessel_imo(f"{VESSEL_ID} RS 150443 (Russia); MMSI 273385420") is None
    assert _extract_vessel_imo(f"{VESSEL_ID} 9894387; Linked To: Z.") is None
    # Fails the IMO check digit, so it's a typo, not an IMO number.
    assert _extract_vessel_imo(f"{VESSEL_ID} IMO 9113378") is None
    assert _extract_vessel_imo(None) is None


def test_parse_sdn_csv_fills_imo_number() -> None:
    csv_text = (
        "1,LUCKY STAR,vessel,IRAN-EO13902,,,Crude Oil Tanker,,,Panama,,"
        '"Vessel Registration Identification IMO 9113379; MMSI 668116259."\n'
        '2,SOME SHIPPING CO,-0-,IRAN,,,,,,,,"Identification Number IMO 5422497."\n'
    )
    df = parse_sdn_csv(csv_text)
    assert df["imo_number"].to_list() == ["9113379", None]


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
