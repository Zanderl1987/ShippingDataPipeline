from __future__ import annotations

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from src.monitoring.collect_all import (
    CollectionReport,
    CollectionResult,
    CollectorDef,
    get_collectors,
    print_collection_report,
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
        assert result.success is False
        assert "No API key" in result.error

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
