from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest

from src.curation.enrichment import (
    create_curated_ais_positions,
    create_curated_vessels,
    enrich_ais_with_port_info,
    enrich_ais_with_vessel_info,
)
from src.storage.writer import init_db, write_raw


@pytest.fixture
def db(tmp_path: Path) -> None:
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()

    yield

    settings.data_dir = old_data
    settings.storage_dir = old_storage


def test_enrich_ais_with_vessel_info(db) -> None:
    init_db()

    vessels_df = pl.DataFrame(
        {
            "imo": [111, 222],
            "mmsi": [123, 456],
            "vessel_name": ["A", "B"],
            "vessel_type": ["Container", "Tanker"],
            "flag": ["PA", "SG"],
            "callsign": ["A", "B"],
            "length_m": [300.0, 200.0],
            "beam_m": [40.0, 30.0],
            "gross_tonnage": [100000.0, 50000.0],
            "deadweight_tonnage": [120000.0, 60000.0],
            "year_built": [2020, 2015],
            "owner_name": ["Owner A", "Owner B"],
            "manager_name": ["Manager A", "Manager B"],
            "source": ["test"] * 2,
        }
    )

    write_raw("test", vessels_df, table_name="vessels")

    ais_df = pl.DataFrame(
        {
            "mmsi": [123, 456],
            "imo": [111, 222],
            "vessel_name": ["A", "B"],
            "latitude": [51.0, 52.0],
            "longitude": [0.1, 0.2],
            "sog": [10.0, 12.0],
            "cog": [90.0, 180.0],
            "heading": [90.0, 180.0],
            "nav_status": ["Under way"] * 2,
            "draught": [5.0] * 2,
            "destination": ["PORT"] * 2,
            "eta": ["2026-01-01"] * 2,
            "timestamp": ["2026-01-01 10:00:00"] * 2,
            "source": ["test"] * 2,
            "partition_date": [date(2026, 1, 1)] * 2,
        }
    )

    write_raw("test", ais_df)

    result = enrich_ais_with_vessel_info()
    assert result.height == 2
    assert "vessel_type" in result.columns
    assert "flag" in result.columns


def test_enrich_ais_with_port_info(db) -> None:
    init_db()

    ports_df = pl.DataFrame(
        {
            "unlocode": ["NLRTM", "SGSIN"],
            "port_name": ["Rotterdam", "Singapore"],
            "country": ["Netherlands", "Singapore"],
            "country_code": ["NL", "SG"],
            "latitude": [51.9, 1.3],
            "longitude": [4.5, 103.8],
            "timezone": ["Europe/Amsterdam", "Asia/Singapore"],
            "region": ["Europe", "Asia"],
            "source": ["test"] * 2,
        }
    )

    write_raw("test", ports_df, table_name="ports")

    ais_df = pl.DataFrame(
        {
            "mmsi": [123, 456],
            "imo": [111, 222],
            "vessel_name": ["A", "B"],
            "latitude": [51.0, 52.0],
            "longitude": [0.1, 0.2],
            "sog": [10.0, 12.0],
            "cog": [90.0, 180.0],
            "heading": [90.0, 180.0],
            "nav_status": ["Under way"] * 2,
            "draught": [5.0] * 2,
            "destination": ["NLRTM", "SGSIN"],
            "eta": ["2026-01-01"] * 2,
            "timestamp": ["2026-01-01 10:00:00"] * 2,
            "source": ["test"] * 2,
            "partition_date": [date(2026, 1, 1)] * 2,
        }
    )

    write_raw("test", ais_df)

    result = enrich_ais_with_port_info()
    assert result.height == 2
    assert "destination_port_name" in result.columns
    assert "destination_country" in result.columns


def test_create_curated_ais_positions(db) -> None:
    init_db()

    vessels_df = pl.DataFrame(
        {
            "imo": [111],
            "mmsi": [123],
            "vessel_name": ["A"],
            "vessel_type": ["Container"],
            "flag": ["PA"],
            "callsign": ["A"],
            "length_m": [300.0],
            "beam_m": [40.0],
            "gross_tonnage": [100000.0],
            "deadweight_tonnage": [120000.0],
            "year_built": [2020],
            "owner_name": ["Owner A"],
            "manager_name": ["Manager A"],
            "source": ["test"],
        }
    )

    write_raw("test", vessels_df, table_name="vessels")

    ports_df = pl.DataFrame(
        {
            "unlocode": ["NLRTM"],
            "port_name": ["Rotterdam"],
            "country": ["Netherlands"],
            "country_code": ["NL"],
            "latitude": [51.9],
            "longitude": [4.5],
            "timezone": ["Europe/Amsterdam"],
            "region": ["Europe"],
            "source": ["test"],
        }
    )

    write_raw("test", ports_df, table_name="ports")

    ais_df = pl.DataFrame(
        {
            "mmsi": [123],
            "imo": [111],
            "vessel_name": ["A"],
            "latitude": [51.0],
            "longitude": [0.1],
            "sog": [10.0],
            "cog": [90.0],
            "heading": [90.0],
            "nav_status": ["Under way"],
            "draught": [5.0],
            "destination": ["NLRTM"],
            "eta": ["2026-01-01"],
            "timestamp": ["2026-01-01 10:00:00"],
            "source": ["test"],
            "partition_date": [date(2026, 1, 1)],
        }
    )

    write_raw("test", ais_df)

    count = create_curated_ais_positions()
    assert count == 1


def test_create_curated_vessels(db) -> None:
    init_db()

    vessels_df = pl.DataFrame(
        {
            "imo": [111],
            "mmsi": [123],
            "vessel_name": ["A"],
            "vessel_type": ["Container"],
            "flag": ["PA"],
            "callsign": ["A"],
            "length_m": [300.0],
            "beam_m": [40.0],
            "gross_tonnage": [100000.0],
            "deadweight_tonnage": [120000.0],
            "year_built": [2020],
            "owner_name": ["Owner A"],
            "manager_name": ["Manager A"],
            "source": ["test"],
        }
    )

    write_raw("test", vessels_df, table_name="vessels")

    count = create_curated_vessels()
    assert count == 1


def _port_days(port_id: str, end: date, calls: list[int]) -> pl.DataFrame:
    """One port_activity row per day, the last one dated `end`."""
    from datetime import timedelta

    n = len(calls)
    days = [end - timedelta(days=n - 1 - i) for i in range(n)]
    return pl.DataFrame(
        {
            "activity_date": days,
            "year": [d.year for d in days],
            "port_id": [port_id] * n,
            "port_name": [port_id.upper()] * n,
            "country": ["Testland"] * n,
            "portcalls": calls,
            "source": ["portwatch"] * n,
            "partition_date": [end] * n,
        }
    )


def test_create_port_congestion_proxy(db) -> None:
    import duckdb

    from src.curation.enrichment import create_port_congestion_proxy
    from src.storage.writer import get_db_path

    init_db()
    end = date(2026, 9, 18)
    # 120 days: 30 days outside the baseline window (value 1000, must be
    # ignored), 83 baseline-only days of 10, then 7 recent days of 20.
    calls_a = [1000] * 30 + [10] * 83 + [20] * 7
    # A port whose data stops earlier keeps its own as-of date.
    calls_b = [5] * 90
    write_raw(
        "portwatch_ports",
        pl.concat(
            [_port_days("port1", end, calls_a), _port_days("port2", date(2026, 9, 1), calls_b)]
        ),
        table_name="port_activity",
    )
    write_raw(
        "portwatch_ports",
        pl.DataFrame({"port_id": ["port1"], "port_name": ["PORT1"], "locode": ["XX AAA"]}),
        table_name="port_profiles",
    )

    assert create_port_congestion_proxy() == 2

    conn = duckdb.connect(str(get_db_path()), read_only=True)
    rows = {
        r[0]: r[1:]
        for r in conn.execute(
            "SELECT port_id, locode, as_of, recent_avg_portcalls_per_day, "
            "baseline_avg_portcalls_per_day, ratio_vs_baseline, baseline_days "
            "FROM port_congestion_proxy"
        ).fetchall()
    }
    conn.close()

    locode, as_of, recent, baseline, ratio, n = rows["port1"]
    assert (locode, as_of, n) == ("XX AAA", end, 90)
    assert recent == pytest.approx(20.0)
    assert baseline == pytest.approx((83 * 10 + 7 * 20) / 90)
    assert ratio == pytest.approx(20.0 / ((83 * 10 + 7 * 20) / 90))

    locode, as_of, recent, baseline, ratio, n = rows["port2"]
    assert (locode, as_of, recent, baseline, ratio) == (None, date(2026, 9, 1), 5.0, 5.0, 1.0)
