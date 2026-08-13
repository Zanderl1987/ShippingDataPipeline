from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.port_la import (
    _to_float,
    parse_port_la_records,
    parse_socrata_historical,
    parse_year_page_html,
)


@pytest.fixture
def mock_settings(tmp_path: Path) -> None:
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    yield
    settings.data_dir = old_data
    settings.storage_dir = old_storage


MOCK_SOCRATA = [
    {"month_year": "Sep-16", "monthly_total_teus": "1,234.50"},
    {"month_year": "Oct-16", "monthly_total_teus": "987.00"},
    {"month_year": "not-a-date", "monthly_total_teus": "500.00"},
]

PAGE_HTML = """<table>
<tr><th>Month</th><th>Loaded Imports</th><th>Empty Imports</th>
<th>Total Imports</th><th>Loaded Exports</th><th>Empty Exports</th>
<th>Total Exports</th><th>Total TEUs</th><th>Prior Year Change</th></tr>
<tr><td>January</td><td>361,108.30</td><td>&nbsp;11,418.00&nbsp;</td>
<td>&nbsp;372,526.30</td><td>&nbsp;164,703.65</td><td>&nbsp;168,306.00&nbsp;</td>
<td>333,009.65</td><td>&nbsp;705,535.95</td><td>-1.29%</td></tr>
<tr><td>November</td><td>464,819.70</td><td>1,247.70</td><td>466,067.40</td>
<td>130,916.50</td><td>292,762.25</td><td>423,678.75</td>
<td>889.,748.15</td><td>22.06%</td></tr>
<tr><td>NotAMonth</td><td>1</td><td>2</td><td>3</td><td>4</td><td>5</td><td>6</td><td>7</td><td>8</td></tr>
</table>"""


def test_to_float_handles_separators() -> None:
    assert _to_float("1,234,567.89") == 1234567.89
    assert _to_float("&nbsp;11,418.00&nbsp;") is None
    assert _to_float("-") is None
    assert _to_float("") is None
    assert _to_float(None) is None


def test_parse_socrata_historical() -> None:
    records = parse_socrata_historical(MOCK_SOCRATA)
    assert len(records) == 2
    assert records[0]["period"] == "2016-09"
    assert records[0]["period_date"].isoformat() == "2016-09-01"
    assert records[0]["total_teu"] == 1234.5
    assert records[1]["total_teu"] == 987.0


def test_parse_socrata_historical_empty() -> None:
    assert parse_socrata_historical([]) == []


def test_parse_year_page_html() -> None:
    records = parse_year_page_html(PAGE_HTML, 2018)
    assert len(records) == 2
    jan = next(r for r in records if r["period"] == "2018-01")
    assert jan["loaded_imports_teu"] == 361108.3
    assert jan["empty_imports_teu"] == 11418.0
    assert jan["total_teu"] == 705535.95
    assert jan["prior_year_change_pct"] == -1.29

    nov = next(r for r in records if r["period"] == "2018-11")
    assert nov["total_teu"] == 466067.4 + 423678.75


def test_parse_year_page_html_empty() -> None:
    assert parse_year_page_html("", 2018) == []


def test_parse_port_la_records() -> None:
    pages = parse_year_page_html(PAGE_HTML, 2018)
    df = parse_port_la_records(MOCK_SOCRATA, pages)
    assert df.height == 4
    assert "port_code" in df.columns
    assert "period_date" in df.columns
    assert "source" in df.columns
    assert set(df["source"].unique().to_list()) == {"port_la"}
    assert set(df["port_code"].unique().to_list()) == {"USLAX"}


@patch("src.collectors.port_la.fetch_year_page", return_value=[])
@patch("src.collectors.port_la.write_raw")
@patch("src.collectors.port_la.fetch_socrata_historical")
def test_collect_port_la(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    _mock_page: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = MOCK_SOCRATA
    mock_write.return_value = 3

    from src.collectors.port_la import collect_port_la_data

    count = collect_port_la_data()
    assert count == 3
    mock_fetch.assert_called_once()
    mock_write.assert_called_once()
