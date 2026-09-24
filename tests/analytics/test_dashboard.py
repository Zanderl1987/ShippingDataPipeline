from __future__ import annotations

from datetime import date, datetime

import duckdb

from src.analytics.dashboard import (
    DashboardData,
    _sparkline,
    build_html,
    generate_data_dashboard,
    load_data,
)
from src.storage.writer import get_db_path

AS_OF = date(2026, 9, 20)


def _seed(conn: duckdb.DuckDBPyConnection) -> None:
    conn.execute(
        "CREATE TABLE chokepoint_daily AS SELECT * FROM (VALUES "
        "(DATE '2026-09-19', 'chokepoint1', 'Suez Canal', 40.0, 80.0, -50.0, -40.0, -45.0, "
        " -5.0, 'TC STORM-26', 'RED'),"
        "(DATE '2026-09-20', 'chokepoint1', 'Suez Canal', 41.0, 80.0, -48.8, -40.0, -45.0, "
        " -5.0, 'TC STORM-26', 'RED'),"
        "(DATE '2026-09-20', 'chokepoint6', 'Strait of Hormuz', 3.1, 90.0, -96.4, -99.0, -98.0, "
        " -29.0, NULL, NULL)"
        ") t(transit_date, chokepoint_id, chokepoint_name, n_total_7d, n_total_7d_year_ago, "
        "n_total_vs_year_ago_pct, n_tanker_vs_year_ago_pct, capacity_vs_year_ago_pct, "
        "n_total_vs_prior_28d_pct, nearby_event_names, nearby_max_alert)"
    )
    conn.execute(
        "CREATE TABLE port_congestion_proxy AS SELECT * FROM (VALUES "
        "('port1', 'Rotterdam', 'Netherlands', DATE '2026-09-18', 100.0, 80.0, 1.25),"
        "('port2', 'Jeddah', 'Saudi Arabia', DATE '2026-09-18', 3.0, 6.0, 0.5),"
        "('port3', 'Tiny', 'Nowhere', DATE '2026-09-18', 1.0, 0.3, 3.3)"
        ") t(port_id, port_name, country, as_of, recent_avg_portcalls_per_day, "
        "baseline_avg_portcalls_per_day, ratio_vs_baseline)"
    )
    conn.execute(
        "INSERT INTO port_profiles (port_id, latitude, longitude) VALUES "
        "('port1', 51.9, 4.5), ('port2', 21.5, 39.2), ('port3', 0, 0)"
    )


def test_load_data_uses_latest_day_and_skips_quiet_ports() -> None:
    with duckdb.connect(str(get_db_path())) as conn:
        _seed(conn)
        data = load_data(conn)
    assert data.chokepoint_as_of == AS_OF
    assert [c["chokepoint_name"] for c in data.chokepoints] == ["Strait of Hormuz", "Suez Canal"]
    assert len(data.sparklines["chokepoint1"]) == 2
    # Tiny averages 0.3 calls a day: one ship would swing its ratio wildly.
    assert [p["port_name"] for p in data.ports] == ["Jeddah", "Rotterdam"]


def test_page_shows_the_numbers_and_escapes_names() -> None:
    with duckdb.connect(str(get_db_path())) as conn:
        _seed(conn)
        conn.execute("UPDATE port_congestion_proxy SET port_name = 'A<b>' WHERE port_id = 'port1'")
        page = build_html(load_data(conn), generated_at=datetime(2026, 9, 24, 7, 0))
    assert "-96%" in page and "RED: TC STORM-26" in page
    assert "Week to 2026-09-20" in page
    assert "A&lt;b&gt;" in page and "A<b>" not in page
    assert "<script" not in page  # self-contained, no JS needed


def test_empty_database_still_renders(tmp_path) -> None:
    out = generate_data_dashboard(tmp_path / "d.html")
    page = out.read_text(encoding="utf-8")
    assert "No chokepoint data yet" in page and "No port activity data yet" in page


def test_sparkline_breaks_at_gaps() -> None:
    svg = _sparkline([(1.0, 2.0), (2.0, None), (None, None), (3.0, 1.0), (4.0, 1.5)])
    # Solid line: two segments around the gap; dashed: one segment at the end.
    assert svg.count("<polyline") == 3
    assert _sparkline([]) == ""


def test_build_html_with_no_data() -> None:
    page = build_html(DashboardData([], {}, [], None, None))
    assert "Not shown yet" in page
