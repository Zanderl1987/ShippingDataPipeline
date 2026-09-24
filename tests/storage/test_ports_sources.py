"""ports keeps one row per code per source; curation merges them per code."""
from __future__ import annotations

import duckdb
import polars as pl

from src.curation.dedup import deduplicate_ports
from src.curation.enrichment import PORTS_BY_CODE
from src.storage.migrations import MIGRATIONS
from src.storage.writer import get_db_path, write_raw


def _port(source: str, **cols: object) -> pl.DataFrame:
    data = {"unlocode": ["FIHEL"], "source": [source]}
    data.update({k: [v] for k, v in cols.items()})
    return pl.DataFrame(data)


def _conn() -> duckdb.DuckDBPyConnection:
    return duckdb.connect(str(get_db_path()))


def test_two_sources_for_one_code_both_kept() -> None:
    # Keyed on the code alone, UN/LOCODE (runs later) replaced Digitraffic's row.
    dt = _port("digitraffic", port_name="Helsinki", country="Finland")
    write_raw("digitraffic", dt, "ports")
    write_raw("unlocode", _port("unlocode", port_name="Helsinki"), "ports")
    write_raw("unlocode", _port("unlocode", port_name="Helsingfors"), "ports")
    with _conn() as conn:
        rows = conn.execute("SELECT source, port_name FROM ports ORDER BY source").fetchall()
    assert rows == [("digitraffic", "Helsinki"), ("unlocode", "Helsingfors")]


def test_merged_view_prefers_unlocode_and_fills_gaps() -> None:
    dt = _port(
        "digitraffic", port_name="Helsinki DT", country="Finland", latitude=60.1, longitude=24.9
    )
    write_raw("digitraffic", dt, "ports")
    # UN/LOCODE has no country names and sometimes no coordinates.
    un = _port("unlocode", port_name="Helsinki", latitude=60.2, longitude=None)
    write_raw("unlocode", un, "ports")
    with _conn() as conn:
        row = conn.execute(
            f"SELECT port_name, country, latitude, longitude FROM {PORTS_BY_CODE}"
        ).fetchall()
    # Name from UN/LOCODE; country from Digitraffic; the coordinate pair from
    # Digitraffic, since UN/LOCODE's is incomplete (never 60.2 with 24.9).
    assert row == [("Helsinki", "Finland", 60.1, 24.9)]


def test_dedup_keeps_one_row_per_source() -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT INTO ports (unlocode, port_name, source) VALUES "
            "('FIHEL', 'a', 'unlocode'), ('FIHEL', 'b', 'unlocode'), "
            "('FIHEL', 'c', 'digitraffic')"
        )
        assert deduplicate_ports(conn) == 1
        assert conn.execute("SELECT count(*) FROM ports").fetchone() == (2,)


def test_migration_drops_the_old_primary_key() -> None:
    migration = next(m for m in MIGRATIONS if m.version == "202609240001")
    insert = "INSERT INTO ports (unlocode, port_name, source) VALUES ('FIHEL', 'Helsinki', ?)"
    conn = duckdb.connect()
    conn.execute(
        "CREATE TABLE ports (unlocode VARCHAR PRIMARY KEY, port_name VARCHAR, "
        "source VARCHAR, ingested_at TIMESTAMP DEFAULT now())"
    )
    conn.execute(insert, ["unlocode"])
    for statement in migration.up_sql.split(";"):
        if statement.strip():
            conn.execute(statement)
    conn.execute(insert, ["digitraffic"])
    assert conn.execute("SELECT count(*), count(ingested_at) FROM ports").fetchone() == (2, 2)
