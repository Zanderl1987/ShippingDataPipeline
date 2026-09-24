from __future__ import annotations

from datetime import datetime, timedelta

import duckdb
import pytest

from src.monitoring.quality_alerts import (
    AlertThresholds,
    check_null_spikes,
    check_row_drops,
    check_run,
    check_stale_sources,
    db_now,
    take_baseline,
    write_github_annotations,
)
from src.storage.writer import get_db_path

T0 = datetime(2026, 9, 24, 6, 0)
RUN = datetime(2026, 9, 25, 6, 0)


@pytest.fixture
def conn():
    # tests/conftest.py creates the schema in a tmp dir.
    c = duckdb.connect(str(get_db_path()))
    yield c
    c.close()


def _weather(conn, n: int, ingested: datetime, wave_height: float | None, source="open_meteo"):
    conn.executemany(
        "INSERT INTO marine_weather (latitude, longitude, timestamp, wave_height, "
        "source, partition_date, ingested_at) VALUES (1, 1, ?, ?, ?, ?, ?)",
        [
            (ingested + timedelta(minutes=i), wave_height, source, ingested.date(), ingested)
            for i in range(n)
        ],
    )


class TestRowDrops:
    def test_loss_over_threshold_alerts(self, conn) -> None:
        _weather(conn, 100, T0, 1.0)
        baseline = take_baseline(conn)
        conn.execute("DELETE FROM marine_weather WHERE timestamp < ?", [T0 + timedelta(minutes=2)])
        alerts = check_row_drops(conn, baseline)
        assert [a.kind for a in alerts] == ["row_drop"]
        assert "100 rows at start, 98 at end" in alerts[0].message

    def test_small_trim_is_allowed(self, conn) -> None:
        # Cleaning and dedup remove a few rows on purpose.
        _weather(conn, 200, T0, 1.0)
        baseline = take_baseline(conn)
        conn.execute("DELETE FROM marine_weather WHERE timestamp = ?", [T0])
        assert check_row_drops(conn, baseline) == []

    def test_tables_empty_at_start_are_ignored(self, conn) -> None:
        baseline = take_baseline(conn)
        _weather(conn, 10, T0, 1.0)
        assert check_row_drops(conn, baseline) == []


class TestNullSpikes:
    def test_field_gone_from_this_runs_rows_alerts(self, conn) -> None:
        _weather(conn, 100, T0, 1.0)
        _weather(conn, 60, RUN, None)
        alerts = check_null_spikes(conn, since=RUN)
        assert len(alerts) == 1
        assert alerts[0].message == (
            "marine_weather.wave_height from open_meteo: 100% null in this run's "
            "60 rows, 0% before"
        )

    def test_column_always_sparse_is_quiet(self, conn) -> None:
        # The old check flagged every column over 50% null, every day.
        _weather(conn, 100, T0, None)
        _weather(conn, 60, RUN, None)
        assert check_null_spikes(conn, since=RUN) == []

    def test_sources_are_compared_separately(self, conn) -> None:
        # A sparse source joining the table isn't a spike in the other.
        _weather(conn, 100, T0, 1.0)
        _weather(conn, 60, RUN, 1.0)
        _weather(conn, 60, RUN, None, source="erddap")
        assert check_null_spikes(conn, since=RUN) == []

    def test_too_few_rows_is_quiet(self, conn) -> None:
        _weather(conn, 100, T0, 1.0)
        _weather(conn, 10, RUN, None)
        assert check_null_spikes(conn, since=RUN) == []


class TestStaleSources:
    def test_source_silent_past_limit_alerts(self, conn) -> None:
        _weather(conn, 5, RUN - timedelta(hours=72), 1.0, source="erddap")
        _weather(conn, 5, RUN, 1.0)
        alerts = check_stale_sources(conn, now=RUN)
        assert [(a.source, a.table) for a in alerts] == [("erddap", "marine_weather")]
        assert alerts[0].message == "erddap last wrote to marine_weather 72h ago (limit 48h)"

    def test_per_source_limit(self, conn) -> None:
        _weather(conn, 5, RUN - timedelta(days=40), 1.0, source="monthly")
        t = AlertThresholds(stale_hours_by_source={"monthly": 60 * 24.0})
        assert check_stale_sources(conn, now=RUN, thresholds=t) == []


def test_check_run_on_a_quiet_run_is_empty(conn) -> None:
    now = db_now(conn)
    _weather(conn, 100, now - timedelta(hours=24), 1.0)
    baseline = take_baseline(conn)
    _weather(conn, 60, baseline.started_at + timedelta(seconds=1), 1.2)
    assert check_run(conn, baseline) == []


def test_annotations_only_in_github_actions(monkeypatch, capsys, tmp_path) -> None:
    from src.monitoring.quality_alerts import Alert

    alert = Alert("stale_source", "marine_weather", "erddap last wrote 72h ago")
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    write_github_annotations([alert])
    assert capsys.readouterr().out == ""

    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    write_github_annotations([alert])
    assert "::warning title=Data alert (stale_source)::erddap last wrote 72h ago" in (
        capsys.readouterr().out
    )
    assert "- erddap last wrote 72h ago" in summary.read_text(encoding="utf-8")
