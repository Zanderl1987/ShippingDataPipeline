from __future__ import annotations

from datetime import date, datetime

import duckdb
import polars as pl
import pytest

from src.curation.cleaning import clean_ais_positions, clean_ports
from src.storage.writer import get_db_path, init_db, write_raw


@pytest.fixture
def conn():
    # tests/conftest.py points storage at a tmp dir.
    init_db().close()
    c = duckdb.connect(str(get_db_path()))
    yield c
    c.close()


def _ais(**overrides: list) -> pl.DataFrame:
    n = len(next(iter(overrides.values()))) if overrides else 1
    base: dict[str, list] = {
        "mmsi": list(range(100, 100 + n)),
        "imo": [None] * n,
        "vessel_name": [f"V{i}" for i in range(n)],
        "latitude": [51.0] * n,
        "longitude": [4.0] * n,
        "sog": [10.0] * n,
        "cog": [90.0] * n,
        "heading": [90.0] * n,
        "timestamp": [datetime(2026, 9, 24, 10, i) for i in range(n)],
        "source": ["digitraffic"] * n,
        "partition_date": [date(2026, 9, 24)] * n,
    }
    base.update(overrides)
    return pl.DataFrame(base)


def _rows(conn, sql: str) -> list[tuple]:
    return conn.execute(sql).fetchall()


class TestCleanAisPositions:
    def test_not_available_codes_become_null(self, conn) -> None:
        # ITU-R M.1371: sog 102.3, cog 360, heading 511, lat 91, lon 181
        # mean "not available".
        write_raw(
            "digitraffic",
            _ais(
                sog=[102.3, 10.0, 10.0, 10.0, 10.0],
                cog=[90.0, 360.0, 90.0, 90.0, 90.0],
                heading=[90.0, 90.0, 511.0, 90.0, 90.0],
                latitude=[51.0, 51.0, 51.0, 91.0, 51.0],
                longitude=[4.0, 4.0, 4.0, 4.0, 181.0],
            ),
        )
        changed = clean_ais_positions(conn)
        assert changed == 5
        assert _rows(
            conn,
            "SELECT sog, cog, heading, latitude, longitude FROM ais_positions ORDER BY mmsi",
        ) == [
            (None, 90.0, 90.0, 51.0, 4.0),
            (10.0, None, 90.0, 51.0, 4.0),
            (10.0, 90.0, None, 51.0, 4.0),
            (10.0, 90.0, 90.0, None, 4.0),
            (10.0, 90.0, 90.0, 51.0, None),
        ]

    def test_top_encodable_speed_is_kept(self, conn) -> None:
        # 102.2 means "102.2 kn or more", a real (if rare) reading; anything
        # above it can't come from AIS at all.
        write_raw("digitraffic", _ais(sog=[102.2, 103.0, 131.0, 0.0]))
        clean_ais_positions(conn)
        assert _rows(conn, "SELECT sog FROM ais_positions ORDER BY mmsi") == [
            (102.2,),
            (None,),
            (None,),
            (0.0,),
        ]

    def test_rows_missing_mmsi_or_timestamp_are_deleted(self, conn) -> None:
        write_raw(
            "digitraffic",
            _ais(
                mmsi=[1, None, 3],
                vessel_name=["A", "B", "C"],
                timestamp=[datetime(2026, 9, 24, 1), datetime(2026, 9, 24, 2), None],
            ),
        )
        assert clean_ais_positions(conn) == 2
        assert _rows(conn, "SELECT mmsi FROM ais_positions") == [(1,)]

    def test_axiomancer_rows_without_mmsi_or_timestamp_are_kept(self, conn) -> None:
        # Axiomancer never reports mmsi or a timestamp (see schema.py).
        write_raw(
            "axiomancer",
            _ais(
                mmsi=[None, None],
                imo=[9000001, 9000002],
                timestamp=[None, None],
                source=["axiomancer", "axiomancer"],
            ),
        )
        assert clean_ais_positions(conn) == 0
        assert _rows(conn, "SELECT count(*) FROM ais_positions") == [(2,)]

    def test_clean_data_is_untouched(self, conn) -> None:
        write_raw("digitraffic", _ais(sog=[0.0, 12.5], cog=[0.0, 359.9]))
        assert clean_ais_positions(conn) == 0


class TestCleanPorts:
    def test_out_of_range_coordinates_are_nulled_together(self, conn) -> None:
        # UN/LOCODE's own file has typos like Mironovka at longitude 381.8;
        # when one half of a pair is impossible the other can't be trusted.
        df = pl.DataFrame(
            {
                "unlocode": ["UAMIR", "NLRTM"],
                "port_name": ["Mironovka", "Rotterdam"],
                "country_code": ["UA", "NL"],
                "latitude": [48.48, 51.9],
                "longitude": [381.83, 4.48],
                "source": ["unlocode", "unlocode"],
            }
        )
        write_raw("unlocode", df, table_name="ports")
        assert clean_ports(conn) == 1
        assert _rows(
            conn, "SELECT unlocode, latitude, longitude FROM ports ORDER BY unlocode"
        ) == [("NLRTM", 51.9, 4.48), ("UAMIR", None, None)]
