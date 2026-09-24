from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.monitoring.collect_all import (
    EXIT_PARTIAL,
    CollectionReport,
    CollectionResult,
    CollectorDef,
    exit_code,
    get_collectors,
    print_collection_report,
    run_all_collectors,
    run_collector,
)


class TestCollectionResult:
    def test_result_creation(self) -> None:
        """Test collection result creation."""
        result = CollectionResult(
            source="test",
            success=True,
            rows_fetched=100,
            rows_written=95,
            duration_ms=1500,
        )
        assert result.source == "test"
        assert result.success is True
        assert result.rows_fetched == 100
        assert result.rows_written == 95
        assert result.duration_ms == 1500


class TestCollectionReport:
    def test_report_counts(self) -> None:
        """Test report counting."""
        report = CollectionReport(started_at=datetime.now())
        report.results = [
            CollectionResult(source="a", success=True),
            CollectionResult(source="b", success=False),
            CollectionResult(source="c", success=True),
        ]
        assert report.succeeded == 2
        assert report.failed == 1

    def test_report_total_rows(self) -> None:
        """Test total rows calculation."""
        report = CollectionReport(started_at=datetime.now())
        report.results = [
            CollectionResult(source="a", success=True, rows_written=100),
            CollectionResult(source="b", success=True, rows_written=200),
        ]
        assert report.total_rows == 300


class TestExitCode:
    """CI publishes on EXIT_PARTIAL but not on a crash, so the two must differ."""

    def test_all_ok(self) -> None:
        report = CollectionReport(started_at=datetime.now())
        report.results = [CollectionResult(source="a", success=True)]
        assert exit_code(report) == 0

    def test_failed_collector_is_partial(self) -> None:
        report = CollectionReport(started_at=datetime.now())
        report.results = [
            CollectionResult(source="a", success=True),
            CollectionResult(source="b", success=False),
        ]
        assert exit_code(report) == EXIT_PARTIAL

    def test_curation_error_is_partial(self) -> None:
        report = CollectionReport(started_at=datetime.now())
        report.curation_errors = ["boom"]
        assert exit_code(report) == EXIT_PARTIAL

    def test_partial_is_not_python_crash_code(self) -> None:
        # An uncaught exception exits 1; that must never be read as "publish".
        assert EXIT_PARTIAL not in (0, 1)


class TestCollectors:
    def test_get_collectors(self) -> None:
        """Test getting collector definitions."""
        collectors = get_collectors()
        assert len(collectors) > 0
        assert all(isinstance(c, CollectorDef) for c in collectors)

    def test_collector_def_structure(self) -> None:
        """Test collector definition structure."""
        collector = CollectorDef(
            name="test",
            collect_fn=lambda: None,
            requires_key=None,
            schedule="daily",
        )
        assert collector.name == "test"
        assert collector.schedule == "daily"
        assert collector.requires_key is None


class TestRunCollector:
    def test_run_collector_success(self) -> None:
        """Test successful collector run."""
        mock_fn = MagicMock()
        mock_fn.return_value = None

        collector = CollectorDef(
            name="test",
            collect_fn=mock_fn,
        )

        mock_tracker = MagicMock()
        mock_tracker.get_staleness_hours.return_value = None
        mock_tracker.get_last_collection.return_value = {
            "rows_fetched": 100,
            "rows_written": 95,
        }

        mock_notifier = MagicMock()
        mock_notifier.send.return_value = {"log": True}

        result = run_collector(collector, mock_tracker, mock_notifier, force=True)
        assert result.success is True
        mock_fn.assert_called_once()

    def test_run_collector_skip_recent(self) -> None:
        """Test skipping recently collected source."""
        mock_fn = MagicMock()

        collector = CollectorDef(
            name="test",
            collect_fn=mock_fn,
            schedule="daily",
        )

        mock_tracker = MagicMock()
        mock_tracker.get_staleness_hours.return_value = 0.5  # 30 minutes ago

        mock_notifier = MagicMock()

        result = run_collector(collector, mock_tracker, mock_notifier, force=False)
        assert result.success is True
        assert result.error == "Skipped (recently collected)"
        mock_fn.assert_not_called()

    def test_run_collector_skip_no_key(self) -> None:
        """Test skipping when no API key."""
        collector = CollectorDef(
            name="test",
            collect_fn=MagicMock(),
            requires_key="nonexistent_key",
        )

        mock_tracker = MagicMock()
        mock_notifier = MagicMock()

        result = run_collector(collector, mock_tracker, mock_notifier)
        # An unregistered key is a configuration state, not a pipeline failure —
        # some sources (hormuz) can never be configured, so counting this as a
        # failure would keep the scheduled run permanently red.
        assert result.success is True
        assert result.skipped is True
        assert "no API key" in result.error

    def test_run_collector_error(self) -> None:
        """Test collector error handling."""
        mock_fn = MagicMock(side_effect=ValueError("Test error"))

        collector = CollectorDef(
            name="test",
            collect_fn=mock_fn,
        )

        mock_tracker = MagicMock()
        mock_tracker.get_staleness_hours.return_value = None

        mock_notifier = MagicMock()

        result = run_collector(collector, mock_tracker, mock_notifier, force=True)
        assert result.success is False
        assert "Test error" in result.error


class TestRunAllCollectorsCuration:
    """2026-09-07 code review fix: run_all_collectors() -- the function CI's
    collect.yml actually invokes -- never called src.curation.pipeline's
    run_curation() at all, so dedup/validation/enrichment silently never ran
    in production. run_curation=True (the new default) must wire it in."""

    @pytest.fixture
    def isolated_storage(self, tmp_path: Path):
        from src.config import settings

        old_data = settings.data_dir
        old_storage = settings.storage_dir
        settings.data_dir = tmp_path / "data"
        settings.storage_dir = tmp_path / "storage"
        settings.ensure_dirs()
        yield
        settings.data_dir = old_data
        settings.storage_dir = old_storage

    def test_curation_runs_by_default_and_populates_report(self, isolated_storage) -> None:
        report = run_all_collectors(sources=["__no_such_collector__"], notify=False)
        # No real collectors matched, but curation must still have run (on an
        # empty freshly-init'd DB) and reported a clean (error-free) result.
        assert report.curation_errors == []

    def test_curation_can_be_disabled(self, isolated_storage, monkeypatch) -> None:
        called = {"ran": False}

        def fake_run_curation(*args, **kwargs):
            called["ran"] = True
            raise AssertionError("run_curation must not be called when run_curation=False")

        monkeypatch.setattr(
            "src.curation.pipeline.run_curation", fake_run_curation
        )
        run_all_collectors(
            sources=["__no_such_collector__"], notify=False, run_curation=False
        )
        assert called["ran"] is False

    def test_curation_failure_is_recorded_not_swallowed(
        self, isolated_storage, monkeypatch
    ) -> None:
        def boom(*args, **kwargs):
            raise RuntimeError("curation blew up")

        monkeypatch.setattr("src.curation.pipeline.run_curation", boom)
        report = run_all_collectors(sources=["__no_such_collector__"], notify=False)
        assert any("curation blew up" in e for e in report.curation_errors)


class TestPrintReport:
    def test_print_report(self, capsys: pytest.CaptureFixture[str]) -> None:
        """Test report printing."""
        report = CollectionReport(
            started_at=datetime.now(),
            completed_at=datetime.now(),
            results=[
                CollectionResult(
                    source="test",
                    success=True,
                    rows_fetched=100,
                    rows_written=95,
                    duration_ms=1500,
                )
            ],
        )
        print_collection_report(report)
        captured = capsys.readouterr()
        assert "COLLECTION REPORT" in captured.out
        assert "test" in captured.out


class TestTrackerNameMismatch:
    """A collector that records under a name other than its registration
    reported 0 rows while writing data (erddap_marine -> "erddap")."""

    def _run(self, record_as: str, caplog: pytest.LogCaptureFixture) -> CollectionResult:
        from src.storage.tracker import SourceTracker

        tracker = SourceTracker()

        def collect() -> None:
            tracker.record_collection(record_as, rows_fetched=9, rows_written=9)

        return run_collector(
            CollectorDef(name="registered", collect_fn=collect),
            tracker,
            MagicMock(),
            force=True,
        )

    def test_matching_name_reports_counts(self, caplog: pytest.LogCaptureFixture) -> None:
        result = self._run("registered", caplog)
        assert (result.rows_fetched, result.rows_written) == (9, 9)
        assert "recorded no tracker row" not in caplog.text

    def test_mismatched_name_warns(self, caplog: pytest.LogCaptureFixture) -> None:
        result = self._run("something_else", caplog)
        assert (result.rows_fetched, result.rows_written) == (0, 0)
        assert "registered recorded no tracker row" in caplog.text

    def test_stale_row_is_not_reused(self, caplog: pytest.LogCaptureFixture) -> None:
        from src.storage.tracker import SourceTracker

        # A previous run's row under the right name must not be reported as
        # this run's counts.
        SourceTracker().record_collection("registered", rows_fetched=5, rows_written=5)
        result = self._run("something_else", caplog)
        assert (result.rows_fetched, result.rows_written) == (0, 0)
        assert "recorded no tracker row" in caplog.text
