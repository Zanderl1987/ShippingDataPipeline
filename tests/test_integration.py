"""Integration tests for collector-to-storage flow.

Tests the full pipeline: fetch (mocked) -> parse -> write_raw -> verify in DuckDB.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import duckdb
import pytest

from src.config import settings


@pytest.fixture
def mock_settings(tmp_path: Path) -> None:
    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    yield
    settings.data_dir = old_data
    settings.storage_dir = old_storage


@pytest.fixture
def init_db() -> None:
    from src.storage.writer import init_db

    init_db()


# ── Mock data ─────────────────────────────────────────────────────────────────

MOCK_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [-46.30, -23.97]},
            "properties": {
                "imo": "9876543",
                "name": "TEST CARRIER",
                "vessel_type": "bulk_carrier",
                "flag": "PA",
                "speed": 12.4,
                "course": 87,
                "draft": 11.2,
                "destination": "BR SSZ",
                "nav_status": "under way using engine",
                "timestamp": "2026-04-27T18:42:11Z",
            },
        },
    ],
}

MOCK_PORT_POSITIONS = [
    {
        "imo_number": "1234567",
        "name": "TEST TANKER",
        "vessel_type": "tanker",
        "latitude": 51.9,
        "longitude": 4.0,
        "speed": 0.0,
        "course": 0,
        "draft": 14.5,
        "nav_status": "at anchor",
        "destination": "NLRTM",
    },
]

MOCK_VESSELAPI_RESPONSE = {
    "portEvents": [
        {
            "vessel": {
                "imo": 9876543,
                "mmsi": 123456789,
                "name": "TEST VESSEL",
            },
            "port": {
                "unlocode": "NLRTM",
                "name": "Rotterdam",
                "country": "Netherlands",
            },
            "type": "arrival",
            "time": "2026-04-27T18:42:11Z",
            "eta": "2026-04-27T18:00:00Z",
            "etd": None,
            "previousPort": "DEHAM",
            "nextPort": "GBFXT",
        },
    ]
}

MOCK_GFW_VESSELS = {
    "entries": [
        {
            "id": "v123456",
            "registryInfo": [
                {
                    "imo": 9876543,
                    "mmsi": 123456789,
                    "name": "FISHING VESSEL 1",
                    "shipType": "Fishing",
                    "flag": "NOR",
                },
            ],
        },
    ]
}

MOCK_GFW_EVENTS = {
    "entries": [
        {
            "id": "e123456",
            "vessel": {"imo": 9876543, "mmsi": 123456789, "name": "FISHING VESSEL 1"},
            "type": "FISHING",
            "start": "2026-04-27T10:00:00Z",
            "end": "2026-04-27T18:00:00Z",
            "position": {"lat": 60.0, "lon": 5.0},
        },
    ]
}

MOCK_SHIPLOOKUP_SEARCH = {
    "data": [
        {
            "imo": 9876543,
            "mmsi": 123456789,
            "vesselName": "TEST SHIP",
            "shipType": "Bulk Carrier",
            "flag": "PA",
        },
    ]
}

MOCK_SHIPLOOKUP_VESSEL = {
    "success": True,
    "data": {
        "imo": 9876543,
        "mmsi": 123456789,
        "vesselName": "TEST SHIP",
        "shipType": "Bulk Carrier",
        "flag": "PA",
        "length": 229,
        "beam": 32,
        "grossTonnage": 40000,
        "yearBuilt": 2014,
    },
}

MOCK_COMTRADE_DATA = {
    "data": [
        {
            "rtCode": 156,
            "ptCode": 842,
            "cmdCode": "270900",
            "flowCode": "M",
            "period": 2024,
            "primaryValue": 15000000,
            "netWgt": 5000000,
            "grossWgt": 5100000,
        },
    ]
}

MOCK_BARENTSWATCH_GEOJSON = {
    "type": "FeatureCollection",
    "features": [
        {
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [20.0, 70.5]},
            "properties": {
                "mmsi": 123456789,
                "imo": 9876543,
                "shipname": "NORWEGIAN SHIP",
                "shiptype": "Cargo",
                "sog": 10.0,
                "cog": 45.0,
                "heading": 45.0,
                "navstatus": "Under way using engine",
                "destination": "TROMSOE",
                "timestamp": "2026-04-27T18:42:11Z",
            },
        },
    ],
}


# ── Axiomancer Tests ──────────────────────────────────────────────────────────


@patch("src.collectors.axiomancer.fetch_positions_latest")
def test_axiomancer_global_snapshot_to_db(
    mock_fetch: MagicMock,
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.axiomancer import _parse_global_snapshot

    mock_fetch.return_value = MOCK_GEOJSON
    raw = mock_fetch.return_value
    df = _parse_global_snapshot(raw)

    assert df.height == 1
    assert "imo" in df.columns

    from src.storage.writer import write_raw

    count = write_raw("axiomancer", df)
    assert count == 1

    from src.storage.writer import get_db_path

    conn = duckdb.connect(str(get_db_path()))
    result = conn.execute(
        "SELECT imo, vessel_name, source FROM ais_positions WHERE imo = 9876543"
    ).fetchone()
    conn.close()

    assert result is not None
    assert result[0] == 9876543
    assert result[1] == "TEST CARRIER"
    assert result[2] == "axiomancer"


@patch("src.collectors.axiomancer.fetch_port_positions")
def test_axiomancer_port_positions_to_db(
    mock_fetch: MagicMock,
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.axiomancer import _parse_port_positions

    mock_fetch.return_value = MOCK_PORT_POSITIONS
    raw = mock_fetch.return_value
    df = _parse_port_positions(raw)

    assert df.height == 1

    from src.storage.writer import write_raw

    count = write_raw("axiomancer", df)
    assert count == 1

    from src.storage.writer import get_db_path

    conn = duckdb.connect(str(get_db_path()))
    result = conn.execute(
        "SELECT imo, vessel_name FROM ais_positions WHERE imo = 1234567"
    ).fetchone()
    conn.close()

    assert result is not None
    assert result[0] == 1234567
    assert result[1] == "TEST TANKER"


# ── VesselAPI Tests ───────────────────────────────────────────────────────────


def test_vesselapi_port_events_to_db(
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.vesselapi import _parse_port_events

    df = _parse_port_events(MOCK_VESSELAPI_RESPONSE)

    assert df.height == 1
    assert "imo" in df.columns

    from src.storage.writer import write_raw

    count = write_raw("vesselapi", df, "port_calls")
    assert count == 1

    from src.storage.writer import get_db_path

    conn = duckdb.connect(str(get_db_path()))
    row = conn.execute(
        "SELECT imo, vessel_name, port_unlocode FROM port_calls WHERE imo = 9876543"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 9876543
    assert row[1] == "TEST VESSEL"
    assert row[2] == "NLRTM"


# ── Global Fishing Watch Tests ────────────────────────────────────────────────


def test_gfw_vessels_to_db(
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.global_fishing_watch import _parse_vessel_search

    df = _parse_vessel_search(MOCK_GFW_VESSELS)

    assert df.height == 1
    assert "imo" in df.columns
    assert "vessel_name" in df.columns

    from src.storage.writer import write_raw

    count = write_raw("global_fishing_watch", df, "vessels")
    assert count == 1

    from src.storage.writer import get_db_path

    conn = duckdb.connect(str(get_db_path()))
    row = conn.execute(
        "SELECT imo, vessel_name FROM vessels WHERE imo = 9876543"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 9876543
    assert row[1] == "FISHING VESSEL 1"


def test_gfw_events_to_db(
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.global_fishing_watch import _parse_events

    df = _parse_events(MOCK_GFW_EVENTS)

    assert df.height == 1
    assert "event_type" in df.columns
    assert "imo" in df.columns


# ── ShipLookup Tests ──────────────────────────────────────────────────────────


def test_shiplookup_search_to_db(
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.shiplookup import _parse_ship_search

    df = _parse_ship_search(MOCK_SHIPLOOKUP_SEARCH)

    assert df.height == 1
    assert "imo" in df.columns

    from src.storage.writer import write_raw

    count = write_raw("shiplookup", df, "vessels")
    assert count == 1

    from src.storage.writer import get_db_path

    conn = duckdb.connect(str(get_db_path()))
    row = conn.execute(
        "SELECT imo, vessel_name FROM vessels WHERE imo = 9876543"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 9876543


def test_shiplookup_vessel_to_db(
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.shiplookup import _parse_ship_detail

    df = _parse_ship_detail(MOCK_SHIPLOOKUP_VESSEL)

    assert df.height == 1

    from src.storage.writer import write_raw

    count = write_raw("shiplookup", df, "vessels")
    assert count == 1

    from src.storage.writer import get_db_path

    conn = duckdb.connect(str(get_db_path()))
    row = conn.execute(
        "SELECT imo, vessel_name, vessel_type FROM vessels WHERE imo = 9876543"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 9876543
    assert row[2] == "Bulk Carrier"


# ── UN Comtrade Tests ─────────────────────────────────────────────────────────


def test_un_comtrade_to_db(
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.un_comtrade import _parse_trade_data

    df = _parse_trade_data(MOCK_COMTRADE_DATA)

    assert df.height == 1
    assert "reporter_code" in df.columns

    from src.storage.writer import write_raw

    count = write_raw("un_comtrade", df, "trade_flow")
    assert count == 1

    from src.storage.writer import get_db_path

    conn = duckdb.connect(str(get_db_path()))
    row = conn.execute(
        "SELECT reporter_code, partner_code, trade_value_usd "
        "FROM trade_flow WHERE reporter_code = 156"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 156
    assert row[1] == 842
    assert row[2] == 15000000


# ── BarentsWatch Tests ────────────────────────────────────────────────────────


def test_barentswatch_positions_to_db(
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.barentswatch import _parse_positions

    df = _parse_positions(MOCK_BARENTSWATCH_GEOJSON)

    assert df.height == 1
    assert "imo" in df.columns
    assert "vessel_name" in df.columns

    from src.storage.writer import write_raw

    count = write_raw("barentswatch", df, "ais_positions")
    assert count == 1

    from src.storage.writer import get_db_path

    conn = duckdb.connect(str(get_db_path()))
    row = conn.execute(
        "SELECT imo, vessel_name, latitude FROM ais_positions WHERE imo = 9876543"
    ).fetchone()
    conn.close()

    assert row is not None
    assert row[0] == 9876543
    assert row[1] == "NORWEGIAN SHIP"
    assert row[2] == 70.5


# ── Source Tracking Tests ─────────────────────────────────────────────────────


@patch("src.collectors.axiomancer.fetch_positions_latest")
def test_source_tracking_records_collection(
    mock_fetch: MagicMock,
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.axiomancer import collect_global_snapshot
    from src.storage.tracker import SourceTracker

    mock_fetch.return_value = MOCK_GEOJSON
    tracker = SourceTracker()

    collect_global_snapshot(tracker=tracker)

    last = tracker.get_last_collection("axiomancer")
    assert last is not None
    assert last["rows_fetched"] == 1
    assert last["rows_written"] == 1
    assert last["duration_ms"] is not None

    tracker.close()


@patch("src.collectors.axiomancer.fetch_positions_latest")
def test_source_tracking_records_error(
    mock_fetch: MagicMock,
    mock_settings: None,
    init_db: None,
) -> None:
    from src.collectors.axiomancer import collect_global_snapshot
    from src.storage.tracker import SourceTracker

    mock_fetch.side_effect = ConnectionError("API unreachable")
    tracker = SourceTracker()

    try:
        collect_global_snapshot(tracker=tracker)
    except ConnectionError:
        pass

    last = tracker.get_last_collection("axiomancer")
    assert last is None

    from src.storage.writer import get_db_path

    conn = duckdb.connect(str(get_db_path()))
    result = conn.execute(
        "SELECT status, error_message FROM source_tracking "
        "WHERE source = 'axiomancer' ORDER BY collection_ts DESC LIMIT 1"
    ).fetchone()
    conn.close()

    assert result is not None
    assert result[0] == "error"
    assert "unreachable" in result[1]

    tracker.close()
