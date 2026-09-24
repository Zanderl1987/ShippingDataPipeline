from __future__ import annotations

from datetime import date, datetime, timedelta

import duckdb
import pytest

from src.analytics.chokepoint_daily import create_chokepoint_daily
from src.storage.writer import get_db_path

START = date(2024, 1, 1)
SUEZ = ("chokepoint1", "Suez Canal", 30.59, 32.44)


@pytest.fixture
def conn():
    # tests/conftest.py creates the schema in a tmp dir.
    c = duckdb.connect(str(get_db_path()))
    c.execute(
        "INSERT INTO chokepoint_profiles (chokepoint_id, chokepoint_name, latitude, longitude, "
        "source) VALUES (?, ?, ?, ?, 'imf_portwatch_chokepoint_profiles')",
        list(SUEZ),
    )
    yield c
    c.close()


def _transits(conn, days: int, n_total, tankers=None) -> None:
    """One row a day from START; n_total(day_index) gives the count."""
    conn.executemany(
        "INSERT INTO chokepoint_transits (transit_date, chokepoint_id, chokepoint_name, "
        "n_total, n_tanker, capacity, source) VALUES (?, ?, ?, ?, ?, ?, 'imf_portwatch')",
        [
            (START + timedelta(days=i), SUEZ[0], SUEZ[1], n_total(i),
             tankers(i) if tankers else 0, 1000.0 * n_total(i))
            for i in range(days)
        ],
    )


def _event(conn, name, lat, lon, start, end, source="imf_portwatch_disruptions", level="Red"):
    conn.execute(
        "INSERT INTO disruption_events (event_id, event_type, event_name, alert_level, "
        "latitude, longitude, from_date, to_date, source) VALUES (?, 'TC', ?, ?, ?, ?, ?, ?, ?)",
        [f"{name}-{source}", name, level, lat, lon, start, end, source],
    )


def _day(conn, i: int) -> dict:
    cur = conn.execute(
        "SELECT * FROM chokepoint_daily WHERE transit_date = ?", [START + timedelta(days=i)]
    )
    cols = [d[0] for d in cur.description]
    return dict(zip(cols, cur.fetchone(), strict=True))


def test_one_row_per_chokepoint_day(conn) -> None:
    _transits(conn, 30, lambda i: 10)
    assert create_chokepoint_daily(conn) == 30


def test_averages_wait_for_full_windows(conn) -> None:
    _transits(conn, 40, lambda i: i)
    create_chokepoint_daily(conn)
    assert _day(conn, 5)["n_total_7d"] is None
    assert _day(conn, 6)["n_total_7d"] == 3.0  # mean of 0..6
    # The prior-28-day window (days -34..-7) is complete from day 34.
    assert _day(conn, 33)["n_total_vs_prior_28d_pct"] is None
    # 7d mean of 28..34 = 31; prior 28 days 0..27 mean = 13.5.
    assert _day(conn, 34)["n_total_vs_prior_28d_pct"] == round(100 * (31 / 13.5 - 1), 1)


def test_year_ago_compares_same_weekdays(conn) -> None:
    # 100 ships a day for the first year, 50 after: -50% once the window has
    # fully turned over, 364 days back so weekdays line up.
    _transits(conn, 380, lambda i: 100 if i < 364 else 50, tankers=lambda i: 10)
    create_chokepoint_daily(conn)
    row = _day(conn, 370)
    assert row["n_total_7d_year_ago"] == 100
    assert row["n_total_vs_year_ago_pct"] == -50.0
    assert row["capacity_vs_year_ago_pct"] == -50.0
    assert row["n_tanker_vs_year_ago_pct"] == 0.0
    assert _day(conn, 300)["n_total_vs_year_ago_pct"] is None


def test_nearby_hazard_matched_on_dates_and_distance(conn) -> None:
    _transits(conn, 20, lambda i: 10)
    d = datetime.combine(START, datetime.min.time())
    # ~110 km from Suez, days 5-7; listed by both PortWatch feeds.
    _event(conn, "NEAR-24", 31.5, 32.4, d + timedelta(days=5), d + timedelta(days=7))
    _event(conn, "NEAR-24", 31.5, 32.4, d + timedelta(days=5), d + timedelta(days=7),
           source="imf_portwatch_geopulse", level="RED")
    # ~1,100 km away, same days.
    _event(conn, "FAR-24", 40.5, 32.4, d + timedelta(days=5), d + timedelta(days=7))
    create_chokepoint_daily(conn)
    assert _day(conn, 4)["nearby_events"] == 0
    hit = _day(conn, 6)
    assert (hit["nearby_events"], hit["nearby_event_names"], hit["nearby_max_alert"]) == (
        1, "TC NEAR-24", "RED"
    )
    assert _day(conn, 8)["nearby_events"] == 0
