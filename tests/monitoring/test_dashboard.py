from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.monitoring.quality import (
    QualityReport,
    StaleSource,
    TableQuality,
)


@pytest.fixture
def mock_report() -> QualityReport:
    return QualityReport(
        tables=[
            TableQuality(
                table_name="ais_positions",
                row_count=5000,
                null_rates={"latitude": 0.02},
                stale_sources=[],
                partition_count=30,
                last_update=datetime(2026, 7, 27, 12, 0),
            ),
            TableQuality(
                table_name="marine_weather",
                row_count=1200,
                null_rates={},
                stale_sources=[
                    StaleSource(
                        source="noaa",
                        last_partition=datetime(2026, 7, 20, 0, 0),
                        hours_stale=200.0,
                    )
                ],
                partition_count=10,
                last_update=datetime(2026, 7, 20, 0, 0),
            ),
        ],
        total_rows=6200,
        sources_count=40,
        stale_count=1,
        generated_at=datetime(2026, 7, 27, 14, 30),
    )


@pytest.fixture
def mock_sources() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "source": ["noaa", "aisstream"],
            "latest_partition": [
                datetime(2026, 7, 20),
                datetime(2026, 7, 27),
            ],
            "row_count": [1200, 5000],
        }
    )


class TestGenerateDashboard:
    def test_creates_file(self, tmp_path: Path) -> None:
        from src.monitoring.dashboard import generate_dashboard

        out = tmp_path / "out.html"
        with patch("src.monitoring.dashboard.get_quality_report") as mock_qr, \
             patch("src.monitoring.dashboard.check_quality_thresholds", return_value=[]), \
             patch("src.monitoring.dashboard.list_sources") as mock_ls, \
             patch("src.monitoring.dashboard.settings") as mock_settings:
            mock_settings.storage_dir = tmp_path
            mock_qr.return_value = QualityReport(
                tables=[], total_rows=0, sources_count=0,
                stale_count=0, generated_at=datetime.now(),
            )
            mock_ls.return_value = pl.DataFrame(
                {"source": [], "latest_partition": [], "row_count": []}
            )
            result = generate_dashboard(output_path=out)

        assert result == out
        assert out.exists()
        content = out.read_text(encoding="utf-8")
        assert len(content) > 0
        assert content.startswith("<!DOCTYPE html>")

    def test_contains_sections(
        self, tmp_path: Path, mock_report: QualityReport, mock_sources: pl.DataFrame
    ) -> None:
        from src.monitoring.dashboard import generate_dashboard

        out = tmp_path / "dashboard.html"
        with patch("src.monitoring.dashboard.get_quality_report", return_value=mock_report), \
             patch("src.monitoring.dashboard.check_quality_thresholds", return_value=[]), \
             patch("src.monitoring.dashboard.list_sources", return_value=mock_sources), \
             patch("src.monitoring.dashboard.settings") as mock_settings:
            mock_settings.storage_dir = tmp_path
            generate_dashboard(output_path=out)

        html = out.read_text(encoding="utf-8")
        assert "Dashboard" in html
        assert "Pipeline Overview" in html
        assert "Table Summary" in html
        assert "Source Details" in html
        assert "Quality Warnings" in html

    def test_default_path(self, mock_report: QualityReport, mock_sources: pl.DataFrame) -> None:
        from src.monitoring.dashboard import generate_dashboard

        with patch("src.monitoring.dashboard.get_quality_report", return_value=mock_report), \
             patch("src.monitoring.dashboard.check_quality_thresholds", return_value=[]), \
             patch("src.monitoring.dashboard.list_sources", return_value=mock_sources), \
             patch("src.monitoring.dashboard.settings") as mock_settings:
            mock_settings.storage_dir = Path("/tmp/test_storage")
            result = generate_dashboard()

        assert result == Path("/tmp/test_storage/dashboard.html")

    def test_staleness_warning_in_html(
        self, tmp_path: Path, mock_report: QualityReport, mock_sources: pl.DataFrame
    ) -> None:
        from src.monitoring.dashboard import generate_dashboard

        out = tmp_path / "dashboard.html"
        with patch("src.monitoring.dashboard.get_quality_report", return_value=mock_report), \
             patch(
                 "src.monitoring.dashboard.check_quality_thresholds",
                 return_value=["noaa: stale for 200h (threshold: 168h)"],
             ), \
             patch("src.monitoring.dashboard.list_sources", return_value=mock_sources), \
             patch("src.monitoring.dashboard.settings") as mock_settings:
            mock_settings.storage_dir = tmp_path
            generate_dashboard(output_path=out)

        html = out.read_text(encoding="utf-8")
        assert "stale for 200h" in html
        assert "badge-warning" in html
