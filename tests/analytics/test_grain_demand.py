from __future__ import annotations

from datetime import date

import duckdb
import pytest

from src.analytics.grain_demand import (
    create_grain_export_destinations,
    create_grain_export_pace,
    create_grain_trade_monthly,
)
from src.curation.pipeline import run_enrichment
from src.storage.writer import get_db_path


@pytest.fixture
def conn():
    # tests/conftest.py creates the schema in a tmp dir.
    c = duckdb.connect(str(get_db_path()))
    yield c
    c.close()


def _sales(conn, rows: list[tuple]) -> None:
    """rows: (week_ending, marketing_year, commodity, country, shipped, outstanding,
    total_commitments, net_sales)."""
    conn.executemany(
        "INSERT INTO us_export_sales (week_ending, marketing_year, commodity, country, "
        "accumulated_exports, outstanding_sales, total_commitments, net_sales, "
        "weekly_exports, source) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1.0, 'usda_export_sales')",
        rows,
    )


def _rows(conn, sql: str) -> list[dict]:
    cur = conn.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r, strict=True)) for r in cur.fetchall()]


@pytest.fixture
def three_seasons(conn) -> None:
    _sales(conn, [
        # 2023/24: 50 committed in week 1, 100 shipped by the end -> half booked by week 1.
        (date(2023, 9, 7), "2023/2024", "Corn", "MEXICO", 10, 30, 40, 5),
        # No total given: falls back to shipped + outstanding.
        (date(2023, 9, 7), "2023/2024", "Corn", "JAPAN", 0, 10, None, 5),
        (date(2023, 9, 14), "2023/2024", "Corn", "MEXICO", 80, 10, 90, 5),
        (date(2023, 9, 14), "2023/2024", "Corn", "JAPAN", 20, 0, 20, 5),
        # 2024/25: 30 in week 1, 60 by the end -> also half.
        (date(2024, 9, 5), "2024/2025", "Corn", "MEXICO", 0, 30, 30, 5),
        (date(2024, 9, 12), "2024/2025", "Corn", "MEXICO", 60, 0, 60, 5),
        # 2025/26, the season in progress.
        (date(2025, 9, 4), "2025/2026", "Corn", "MEXICO", 0, 45, 45, 5),
        # Not tracked.
        (date(2025, 9, 4), "2025/2026", "Sorghum", "MEXICO", 0, 999, 999, 5),
    ])


def test_pace_compares_same_week_of_earlier_seasons(conn, three_seasons) -> None:
    assert create_grain_export_pace(conn) == 5
    rows = _rows(conn, "SELECT * FROM grain_export_pace ORDER BY week_ending")
    assert {r["commodity"] for r in rows} == {"Corn"}

    first = rows[0]
    assert (first["marketing_year"], first["my_week"]) == ("2023/2024", 1)
    assert first["commitments_mt"] == 50
    assert first["n_destinations"] == 2
    assert first["final_shipped_mt"] == 100
    assert first["commitments_prior_year_mt"] is None

    current = rows[-1]
    assert (current["marketing_year"], current["my_week"]) == ("2025/2026", 1)
    assert current["commitments_prior_year_mt"] == 30
    assert current["commitments_vs_prior_year_pct"] == 50.0
    assert current["commitments_avg5_mt"] == 40
    assert current["commitments_vs_avg5_pct"] == 12.5
    assert current["n_baseline_years"] == 2
    assert current["share_committed_avg5"] == 0.5
    assert current["pace_projection_mt"] == 90  # 45 committed / half typically booked
    assert current["final_shipped_mt"] is None  # season not finished


def test_marketing_year_weeks_line_up_across_years(conn, three_seasons) -> None:
    create_grain_export_pace(conn)
    weeks = _rows(conn, "SELECT marketing_year, week_ending, my_week FROM grain_export_pace")
    # Thursdays fall on different dates each year but in the same 7-day block.
    assert {(r["week_ending"], r["my_week"]) for r in weeks} == {
        (date(2023, 9, 7), 1), (date(2023, 9, 14), 2),
        (date(2024, 9, 5), 1), (date(2024, 9, 12), 2),
        (date(2025, 9, 4), 1),
    }


def test_wheat_season_starts_in_june_and_sums_classes(conn) -> None:
    conn.executemany(
        "INSERT INTO us_export_sales (week_ending, marketing_year, commodity, wheat_class, "
        "country, accumulated_exports, outstanding_sales, total_commitments, net_sales, "
        "weekly_exports, source) VALUES (?, ?, 'Wheat', ?, 'JAPAN', ?, ?, ?, 1, 1, "
        "'usda_export_sales')",
        [
            (date(2025, 6, 5), "2025/2026", "HRW", 10, 20, 30),
            (date(2025, 6, 5), "2025/2026", "White", 5, 5, 10),
            (date(2025, 9, 4), "2025/2026", "HRW", 40, 0, 40),
        ],
    )
    create_grain_export_pace(conn)
    create_grain_export_destinations(conn)
    rows = _rows(conn, "SELECT my_week, commitments_mt FROM grain_export_pace "
                       "ORDER BY week_ending")
    assert [(r["my_week"], r["commitments_mt"]) for r in rows] == [(1, 40), (14, 40)]
    japan = _rows(conn, "SELECT * FROM grain_export_destinations WHERE my_week = 1")
    assert [(r["country"], r["commitments_mt"]) for r in japan] == [("JAPAN", 40)]


def test_destinations_share_and_prior_year(conn, three_seasons) -> None:
    create_grain_export_destinations(conn)
    rows = _rows(conn, """
        SELECT * FROM grain_export_destinations
        WHERE marketing_year = '2023/2024' AND my_week = 1 ORDER BY country
    """)
    assert [(r["country"], r["share_of_commitments_pct"]) for r in rows] == [
        ("JAPAN", 20.0), ("MEXICO", 80.0),
    ]
    mexico = _rows(conn, """
        SELECT * FROM grain_export_destinations
        WHERE marketing_year = '2025/2026' AND country = 'MEXICO'
    """)[0]
    assert mexico["commitments_prior_year_mt"] == 30
    assert mexico["commitments_vs_prior_year_pct"] == 50.0


def _census(conn, rows: list[tuple]) -> None:
    """rows: (period_date, flow_code, export_origin, commodity_code, value, qty, unit)."""
    conn.executemany(
        "INSERT INTO us_trade_products (period_date, year, month, flow_code, export_origin, "
        "commodity_code, value_usd, quantity_1, unit_1, source) "
        "VALUES (?, year(?::DATE), month(?::DATE), ?, ?, ?, ?, ?, ?, 'census_trade')",
        [(r[0], r[0], r[0], *r[1:]) for r in rows],
    )


def test_monthly_converts_units_and_skips_re_exports(conn) -> None:
    _census(conn, [
        (date(2025, 7, 1), "X", "domestic", "1201900095", 400_000, 1000, "T"),
        (date(2026, 7, 1), "X", "domestic", "1201900095", 900_000, 2000, "T"),
        # Seed is reported in kilograms: 500,000 kg = 500 t.
        (date(2026, 7, 1), "X", "domestic", "1201100000", 100_000, 500_000, "KG"),
        # Foreign-origin re-exports are not US demand for US beans.
        (date(2026, 7, 1), "X", "foreign", "1201900095", 1_000_000, 9999, "T"),
        (date(2026, 7, 1), "M", "all", "1507100000", 50_000, 40_000, "KG"),
        # A heading summary row, not an HS10 line.
        (date(2026, 7, 1), "X", "domestic", "1201", 7, 7, "T"),
        (date(2026, 7, 1), "X", "domestic", "8471300100", 5, 5, "NO"),
    ])
    assert create_grain_trade_monthly(conn) == 3
    rows = _rows(conn, """
        SELECT * FROM grain_trade_monthly WHERE period_date = '2026-07-01' ORDER BY product
    """)
    soy = next(r for r in rows if r["product"] == "soybeans")
    assert soy["flow"] == "export"
    assert soy["quantity_mt"] == 2500
    assert soy["value_usd"] == 1_000_000
    assert soy["usd_per_mt"] == 400.0
    assert soy["quantity_prior_year_mt"] == 1000
    assert soy["quantity_vs_prior_year_pct"] == 150.0
    assert soy["usd_per_mt_prior_year"] == 400.0
    oil = next(r for r in rows if r["product"] == "soybean_oil")
    assert (oil["flow"], oil["quantity_mt"]) == ("import", 40)


def test_enrichment_builds_grain_tables(conn, three_seasons) -> None:
    results = run_enrichment(conn)
    assert results["grain_export_pace"] == 5
    assert results["grain_export_destinations"] == 7
    assert results["grain_trade_monthly"] == 0


def test_destinations_usual_share_counts_missing_seasons_as_zero(conn) -> None:
    rows = []
    for year in range(2015, 2021):  # 2015-2019 are the five seasons before 2020
        week1 = date(year, 9, 7)
        rows.append((week1, f"{year}/{year + 1}", "Soybeans", "MEXICO", 0, 75, 75, 1))
        # China bought in only two of the five earlier seasons.
        china = 25 if year in (2016, 2018, 2020) else 0
        if china:
            rows.append((week1, f"{year}/{year + 1}", "Soybeans", "CHINA", 0, china, china, 1))
    _sales(conn, rows)
    create_grain_export_destinations(conn)
    got = {r["country"]: r for r in _rows(conn, """
        SELECT * FROM grain_export_destinations WHERE marketing_year = '2020/2021'
    """)}
    assert got["CHINA"]["share_of_commitments_pct"] == 25.0
    # 25% in 2016 and 2018, absent (0) in 2015, 2017, 2019: 10% on average.
    assert got["CHINA"]["share_avg5_pct"] == 10.0
    assert got["MEXICO"]["share_avg5_pct"] == 90.0
    # Seasons without five earlier ones get no usual share.
    early = _rows(conn, "SELECT share_avg5_pct FROM grain_export_destinations "
                        "WHERE marketing_year = '2019/2020'")
    assert {r["share_avg5_pct"] for r in early} == {None}
