from __future__ import annotations

import io
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors import usda_wasde as mod
from src.collectors.usda_wasde import parse_wasde_csv

HEADER = (
    '"WasdeNumber","ReportDate","ReportTitle","Attribute","ReliabilityProjection",'
    '"Commodity","Region","MarketYear","ProjEstFlag","AnnualQuarterFlag","Value","Unit",'
    '"ReleaseDate","ReleaseTime","ForecastYear","ForecastMonth"\n'
)
CSV = (
    HEADER
    + '"675","September 2026","World Soybean Supply and Use","Exports","","Oilseed, Soybean",'
    '"United States","2026/27","Proj.","Annual","45.86","Million Metric Tons",'
    '"2026-09-11","12:00:00.0000000","2026","9"\n'
    + '"675","September 2026","Reliability of United States September Projections","Exports",'
    '"average","Corn","United States","","","Annual","280.00","Million Bushels",'
    '"2026-09-11","12:00:00.0000000","2026","9"\n'
    + '"675","September 2026","World Soybean Oil Supply and Use","Exports","",'
    '"Soybean Oil","United States","2025/26","Est.","Annual",".44","Million Metric Tons",'
    '"2026-09-11","12:00:00.0000000","2026","9"\n'
    + '"675","September 2026","x","y","","z","w","","","Annual","","Units",'
    '"","12:00:00.0000000","2026","9"\n'
).encode()


@pytest.fixture
def mock_settings(tmp_path: Path) -> None:
    from src.config import settings

    old_data, old_storage = settings.data_dir, settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    yield
    settings.data_dir, settings.storage_dir = old_data, old_storage


def _resp(status: int, body: bytes = b"", ctype: str = "text/csv") -> MagicMock:
    r = MagicMock(status_code=status, content=body, headers={"Content-Type": ctype})
    r.raise_for_status.side_effect = None if status < 400 else RuntimeError(status)
    return r


def test_parse_wasde_csv() -> None:
    df = parse_wasde_csv(CSV)
    assert df.height == 3  # the row without a release date is dropped
    soy = df.row(0, named=True)
    assert soy["release_date"] == date(2026, 9, 11)
    assert soy["release_year"] == 2026
    assert soy["wasde_number"] == 675
    assert soy["commodity"] == "Oilseed, Soybean"
    assert soy["market_year"] == "2026/27"
    assert soy["proj_est_flag"] == "Proj."
    assert soy["value"] == pytest.approx(45.86)
    assert soy["reliability_projection"] is None  # "" -> NULL
    assert df.row(1, named=True)["reliability_projection"] == "average"
    assert df.row(2, named=True)["value"] == pytest.approx(0.44)  # ".44"
    assert set(df["source"]) == {"usda_wasde"}


def test_parse_rejects_changed_layout() -> None:
    with pytest.raises(ValueError, match="missing columns"):
        parse_wasde_csv(b'"WasdeNumber","Value"\n"1","2"\n')


@patch("src.collectors.usda_wasde.requests.get")
def test_fetch_month_prefers_reissue(mock_get: MagicMock) -> None:
    mock_get.side_effect = [_resp(404), _resp(200, CSV)]
    df = mod.fetch_month(2026, 5)
    assert df is not None and df.height == 3
    urls = [c.args[0] for c in mock_get.call_args_list]
    assert urls[0].endswith("2026-05-V3.csv") and urls[1].endswith("2026-05-V2.csv")
    assert "Mozilla" in mock_get.call_args.kwargs["headers"]["User-Agent"]


@patch("src.collectors.usda_wasde.requests.get")
def test_fetch_month_not_published(mock_get: MagicMock) -> None:
    # usda.gov serves its HTML 404 page; a 200 HTML page is not a file either.
    mock_get.side_effect = [_resp(404), _resp(404), _resp(200, b"<html>", "text/html")]
    assert mod.fetch_month(2026, 11) is None


@patch("src.collectors.usda_wasde.requests.get")
def test_fetch_archive_reads_zipped_csv(mock_get: MagicMock) -> None:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("oce-wasde-report-data-2010-04-to-2015-12.csv", CSV)
    mock_get.return_value = _resp(200, buf.getvalue(), "application/zip")
    assert mod.fetch_archive(mod.ARCHIVES[0]).height == 3


@patch("src.collectors.usda_wasde.write_raw", return_value=3)
@patch("src.collectors.usda_wasde.fetch_archive")
@patch("src.collectors.usda_wasde.fetch_month")
def test_collect_recent_only_without_bulk(
    mock_month: MagicMock, mock_archive: MagicMock, mock_write: MagicMock, mock_settings: None,
) -> None:
    mock_month.return_value = parse_wasde_csv(CSV)
    assert mod.collect_wasde(today=date(2026, 10, 1), bulk_backfill=False) == 9
    assert [c.args for c in mock_month.call_args_list] == [(2026, 8), (2026, 9), (2026, 10)]
    mock_archive.assert_not_called()
    assert mock_write.call_args.kwargs["table_name"] == "usda_wasde"


@patch("src.collectors.usda_wasde.write_raw", return_value=3)
@patch("src.collectors.usda_wasde.fetch_archive")
@patch("src.collectors.usda_wasde.fetch_month")
def test_collect_backfills_when_empty(
    mock_month: MagicMock, mock_archive: MagicMock, mock_write: MagicMock, mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_month.return_value = None  # nothing published: skipped, not an error
    mock_archive.return_value = parse_wasde_csv(CSV)
    assert mod.collect_wasde(today=date(2021, 3, 15), bulk_backfill=True) == 6
    assert [c.args[0] for c in mock_archive.call_args_list] == mod.ARCHIVES
    assert [c.args for c in mock_month.call_args_list] == [(2021, 1), (2021, 2), (2021, 3)]
