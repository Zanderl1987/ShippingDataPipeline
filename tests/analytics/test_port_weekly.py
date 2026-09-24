from __future__ import annotations

from datetime import date, timedelta

import duckdb
import pytest

from src.analytics.port_weekly import create_port_weekly
from src.curation.pipeline import run_enrichment
from src.storage.writer import get_db_path

MONDAY = date(2024, 1, 1)


@pytest.fixture
def conn():
    # tests/conftest.py creates the schema in a tmp dir.
    c = duckdb.connect(str(get_db_path()))
    yield c
    c.close()


def _days(conn, port_id: str, days: list[int], calls: int = 2, name: str = "Rotterdam") -> None:
    """One row per day index from MONDAY, each with `calls` calls (1 of them a tanker)."""
    conn.executemany(
        "INSERT INTO port_activity (activity_date, year, port_id, port_name, country, iso3, "
        "portcalls, portcalls_tanker, import_total, export_total, source) "
        "VALUES (?, 2024, ?, ?, 'Netherlands', 'NLD', ?, 1, 100.0, 50.0, 'imf_portwatch')",
        [(MONDAY + timedelta(days=i), port_id, name, calls) for i in days],
    )


def _weeks(conn) -> list[dict]:
    cur = conn.execute("SELECT * FROM port_weekly ORDER BY port_id, week_start")
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


def test_sums_each_monday_to_sunday_week(conn) -> None:
    _days(conn, "port1", list(range(10)))  # a full week, then Mon-Wed
    assert create_port_weekly(conn) == 2
    full, partial = _weeks(conn)
    assert full["week_start"] == MONDAY and full["days_reported"] == 7
    assert full["is_complete_week"] is True
    assert (full["portcalls"], full["portcalls_tanker"]) == (14, 7)
    assert (full["import_total"], full["export_total"]) == (700.0, 350.0)
    # The unfinished week is kept but marked, so it isn't read as a slump.
    assert partial["week_start"] == MONDAY + timedelta(days=7)
    assert (partial["days_reported"], partial["is_complete_week"]) == (3, False)
    assert partial["portcalls"] == 6


def test_gap_in_the_feed_marks_the_week_partial(conn) -> None:
    _days(conn, "port1", [0, 1, 2, 4, 5, 6])  # Thursday missing
    create_port_weekly(conn)
    (week,) = _weeks(conn)
    assert (week["days_reported"], week["is_complete_week"]) == (6, False)


def test_ports_are_kept_apart_and_take_the_latest_name(conn) -> None:
    _days(conn, "port1", [0, 1], name="Old name")
    _days(conn, "port1", [2], name="New name")
    _days(conn, "port2", [0], calls=5, name="Jeddah")
    create_port_weekly(conn)
    weeks = _weeks(conn)
    assert [(w["port_id"], w["port_name"], w["portcalls"]) for w in weeks] == [
        ("port1", "New name", 6),
        ("port2", "Jeddah", 5),
    ]


def test_runs_in_enrichment_on_an_empty_database(conn) -> None:
    errors: list[str] = []
    results = run_enrichment(conn, errors)
    assert results["port_weekly"] == 0
    assert not [e for e in errors if "port_weekly" in e]
