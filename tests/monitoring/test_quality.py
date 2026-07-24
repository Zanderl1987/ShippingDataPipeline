from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

from src.monitoring.quality import (
    QualityReport,
    StaleSource,
    TableQuality,
    check_quality_thresholds,
    get_quality_report,
    get_table_quality,
)
from src.storage.schema import ALL_TABLES


@pytest.fixture
def temp_db(tmp_path: Path) -> Path:
    """Create a temporary database."""
    db_path = tmp_path / "test.db"
    with patch("src.config.settings") as mock_settings:
        mock_settings.storage_dir = tmp_path
        mock_settings.data_dir = tmp_path
        conn = duckdb.connect(str(db_path))
        for table in ALL_TABLES:
            conn.execute(table.create_sql())
        conn.close()
    return db_path


class TestTableQuality:
    def test_empty_table(self, temp_db: Path) -> None:
        """Test quality metrics for empty table."""
        with patch("src.monitoring.quality.get_db_path", return_value=temp_db):
            quality = get_table_quality("ais_positions")
            assert quality is not None
            assert quality.table_name == "ais_positions"
            assert quality.row_count == 0
            assert quality.null_rates == {}

    def test_table_with_data(self, temp_db: Path) -> None:
        """Test quality metrics for table with data."""
        conn = duckdb.connect(str(temp_db))
        conn.execute("""
            INSERT INTO ais_positions (mmsi, vessel_name, source, partition_date)
            VALUES (123456789, 'Test Ship', 'test_source', '2024-01-01')
        """)
        conn.close()

        with patch("src.monitoring.quality.get_db_path", return_value=temp_db):
            quality = get_table_quality("ais_positions")
            assert quality is not None
            assert quality.row_count == 1

    def test_nonexistent_table(self, temp_db: Path) -> None:
        """Test quality for nonexistent table."""
        with patch("src.monitoring.quality.get_db_path", return_value=temp_db):
            quality = get_table_quality("nonexistent_table")
            assert quality is None


class TestQualityReport:
    def test_report_generation(self, temp_db: Path) -> None:
        """Test report generation."""
        with patch("src.monitoring.quality.get_db_path", return_value=temp_db):
            report = get_quality_report()
            assert isinstance(report, QualityReport)
            assert report.total_rows >= 0
            assert isinstance(report.tables, list)

    def test_report_healthy(self) -> None:
        """Test healthy report check."""
        report = QualityReport(
            tables=[],
            total_rows=0,
            sources_count=0,
            stale_count=0,
            generated_at=datetime.now(),
        )
        assert report.is_healthy

    def test_report_unhealthy(self) -> None:
        """Test unhealthy report check."""
        report = QualityReport(
            tables=[],
            total_rows=0,
            sources_count=0,
            stale_count=1,
            generated_at=datetime.now(),
        )
        assert not report.is_healthy


class TestQualityThresholds:
    def test_no_warnings(self) -> None:
        """Test no warnings when thresholds not exceeded."""
        report = QualityReport(
            tables=[
                TableQuality(
                    table_name="test",
                    row_count=100,
                    null_rates={"col1": 0.1},
                    stale_sources=[],
                    partition_count=1,
                    last_update=datetime.now(),
                )
            ],
            total_rows=100,
            sources_count=1,
            stale_count=0,
            generated_at=datetime.now(),
        )
        warnings = check_quality_thresholds(report, max_null_rate=0.5)
        assert len(warnings) == 0

    def test_null_rate_warning(self) -> None:
        """Test warning when null rate exceeds threshold."""
        report = QualityReport(
            tables=[
                TableQuality(
                    table_name="test",
                    row_count=100,
                    null_rates={"col1": 0.6},
                    stale_sources=[],
                    partition_count=1,
                    last_update=datetime.now(),
                )
            ],
            total_rows=100,
            sources_count=1,
            stale_count=0,
            generated_at=datetime.now(),
        )
        warnings = check_quality_thresholds(report, max_null_rate=0.5)
        assert len(warnings) == 1
        assert "col1" in warnings[0]

    def test_stale_source_warning(self) -> None:
        """Test warning for stale source."""
        report = QualityReport(
            tables=[
                TableQuality(
                    table_name="test",
                    row_count=100,
                    null_rates={},
                    stale_sources=[
                        StaleSource(
                            source="test_source",
                            last_partition=datetime.now() - timedelta(days=10),
                            hours_stale=240.0,
                        )
                    ],
                    partition_count=1,
                    last_update=datetime.now() - timedelta(days=10),
                )
            ],
            total_rows=100,
            sources_count=1,
            stale_count=1,
            generated_at=datetime.now(),
        )
        warnings = check_quality_thresholds(report, max_stale_hours=168.0)
        assert len(warnings) == 1
        assert "test_source" in warnings[0]
