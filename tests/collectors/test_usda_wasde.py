from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.collectors import usda_wasde as mod
from src.collectors.usda_wasde import parse_wasde_xml

RELEASED = date(2026, 9, 11)


def _cell(attr: str, value: str) -> str:
    return (f'<m1_attribute_group attribute4="{attr}"><Cell cell_value4="{value}" />'
            "</m1_attribute_group>")


# Trimmed from the September 2026 report: a world table split over two pages,
# with last month's projection shown next to this month's.
XML = f"""<?xml version="1.0" encoding="utf-8"?>
<Report Name="wasde">
 <sr28><Report Name="sr28" page_title=" WASDE - 675 - 28"
   sub_report_title="World Soybean Supply and Use  1/" sub_report_subtitle="(Million Metric Tons)">
  <matrix4 region_header4="2025/26 Est.">
   <m1_region_group2 region4="United States">
    {_cell("Exports", "41.37")}
    {_cell("Ending&#xD;&#xA;Stocks", "8.71")}
   </m1_region_group2>
  </matrix4>
 </Report></sr28>
 <sr29><Report Name="sr29" page_title=" WASDE - 675 - 29"
   sub_report_title="World Soybean Supply and Use  1/ (Cont'd.)"
   sub_report_subtitle="(Million Metric Tons)">
  <matrix1 market_year1="2026/27 (Proj.) ">
   <m1_month_group forecast_month1="Aug">
    <m1_region_group region1="United States">{_cell("Exports", "45.04")}</m1_region_group>
   </m1_month_group>
   <m1_month_group forecast_month1="Sep">
    <m1_region_group region1="United States">{_cell("Exports", "45.86")}</m1_region_group>
    <m1_region_group region1="    Major Exporters  4/">
     {_cell("Exports", "1,175.50")}</m1_region_group>
   </m1_month_group>
  </matrix1>
 </Report></sr29>
 <sr30><Report Name="sr30" page_title=" WASDE - 675 - 30"
   sub_report_title="U.S. Quarterly Animal Product Production" sub_report_subtitle="">
  <matrix1 m1_commodity1="Beef" m1_market_year1="2026" m1_forecast_month1="III">
   <m1_attribute_group m1_attribute1="Production" m1_unit_descr1="Million Pounds">
    <Cell m1_cell_value1="6,614" />
   </m1_attribute_group>
  </matrix1>
 </Report></sr30>
</Report>
""".encode()


@pytest.fixture
def mock_settings(tmp_path: Path) -> None:
    from src.config import settings

    old_data, old_storage = settings.data_dir, settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    yield
    settings.data_dir, settings.storage_dir = old_data, old_storage


def _json_resp(body: dict) -> MagicMock:
    r = MagicMock(status_code=200)
    r.json.return_value = body
    return r


def _release(day: str, name: str) -> dict:
    return {
        "release_datetime": f"{day}T12:00:00+0000",
        "files": [f"https://esmis.example/{name}.pdf", f"https://esmis.example/{name}.xml"],
    }


def test_parse_wasde_xml() -> None:
    df = parse_wasde_xml(XML, RELEASED)
    rows = {(r["report_title"], r["region"], r["market_year"], r["attribute"]): r
            for r in df.to_dicts()}
    assert df.height == 5  # August's projection is dropped
    soy = rows[("World Soybean Supply and Use", "United States", "2026/27", "Exports")]
    assert soy["value"] == pytest.approx(45.86)
    assert soy["commodity"] == "Oilseed, Soybean"
    assert soy["proj_est_flag"] == "Proj."
    assert soy["period"] == "Annual"
    assert soy["unit"] == "Million Metric Tons"
    assert soy["wasde_number"] == 675
    assert soy["reliability_projection"] is None
    assert soy["release_date"] == RELEASED and soy["release_year"] == 2026
    est = rows[("World Soybean Supply and Use", "United States", "2025/26", "Ending Stocks")]
    assert est["proj_est_flag"] == "Est." and est["value"] == pytest.approx(8.71)
    # Footnote marks and padding go; thousands separators are read.
    big = rows[("World Soybean Supply and Use", "Major Exporters", "2026/27", "Exports")]
    assert big["value"] == pytest.approx(1175.5)
    beef = rows[("U.S. Quarterly Animal Product Production", None, "2026", "Production")]
    assert beef["commodity"] == "Beef" and beef["period"] == "III"
    assert beef["unit"] == "Million Pounds" and beef["value"] == pytest.approx(6614)
    assert set(df["source"]) == {"usda_wasde"}


def test_parse_keeps_this_months_projection_in_any_month_format() -> None:
    xml = XML.replace(b'"Sep"', b'"September"')
    df = parse_wasde_xml(xml, RELEASED)
    us = df.filter((df["market_year"] == "2026/27") & (df["region"] == "United States"))
    assert us["value"].to_list() == [pytest.approx(45.86)]


@patch("src.collectors.usda_wasde.requests.get")
def test_list_releases_reads_every_page(mock_get: MagicMock) -> None:
    # ESMIS files a few old releases among newer ones: keep paging past them.
    page = {"pager": {"total_pages": 3}}
    mock_get.side_effect = [
        _json_resp({**page, "results": [_release("2026-09-11", "wasde0926")]}),
        _json_resp({**page, "results": [_release("2004-05-12", "old"),
                                        {"release_datetime": "2014-01-23T12:00:00+0000",
                                         "files": ["https://esmis.example/wasde.asc"]}]}),
        _json_resp({**page, "results": [_release("2018-03-08", "wasde0318"),
                                        _release("2018-03-08", "latest")]}),
    ]
    out = mod.list_releases(since=date(2010, 9, 1))
    assert out == [(date(2026, 9, 11), "https://esmis.example/wasde0926.xml"),
                   (date(2018, 3, 8), "https://esmis.example/wasde0318.xml")]
    assert [c.kwargs["params"]["page"] for c in mock_get.call_args_list] == [0, 1, 2]


@patch("src.collectors.usda_wasde.requests.get")
def test_list_releases_latest_page_only(mock_get: MagicMock) -> None:
    mock_get.return_value = _json_resp(
        {"pager": {"total_pages": 29}, "results": [_release("2026-09-11", "wasde0926")]})
    assert len(mod.list_releases()) == 1
    assert mock_get.call_count == 1


def _report(released: date, _url: str = "") -> pl.DataFrame:
    """The test report as if released on ``released``, with its own report number."""
    df = parse_wasde_xml(XML, released)
    number = (released.year - 2026) * 12 + released.month + 666
    return df.with_columns(pl.lit(number).cast(pl.Int32).alias("wasde_number"))


RELEASES = [(date(2026, 9, 11), "u9"), (date(2026, 8, 12), "u8"),
            (date(2026, 7, 10), "u7"), (date(2026, 6, 11), "u6")]


@patch("src.collectors.usda_wasde.write_raw", return_value=5)
@patch("src.collectors.usda_wasde.fetch_release")
@patch("src.collectors.usda_wasde.list_releases")
def test_collect_recent_only_without_bulk(
    mock_list: MagicMock, mock_fetch: MagicMock, mock_write: MagicMock, mock_settings: None,
) -> None:
    mock_list.return_value = RELEASES
    mock_fetch.side_effect = _report
    assert mod.collect_wasde(bulk_backfill=False) == 15
    assert mock_list.call_args.kwargs == {}
    assert [c.args[0] for c in mock_fetch.call_args_list] == [
        date(2026, 7, 10), date(2026, 8, 12), date(2026, 9, 11)]
    assert mock_write.call_args.kwargs["table_name"] == "usda_wasde"


@patch("src.collectors.usda_wasde._stored_releases")
@patch("src.collectors.usda_wasde.write_raw", return_value=5)
@patch("src.collectors.usda_wasde.fetch_release")
@patch("src.collectors.usda_wasde.list_releases")
def test_collect_backfills_missing_releases(
    mock_list: MagicMock, mock_fetch: MagicMock, mock_write: MagicMock,
    mock_stored: MagicMock, mock_settings: None,
) -> None:
    mock_list.return_value = RELEASES
    # Everything stored: only the latest three are re-fetched, for corrections.
    mock_stored.return_value = {d: 670 + i for i, (d, _) in enumerate(RELEASES)}
    mock_fetch.side_effect = _report
    mod.collect_wasde(bulk_backfill=True)
    assert mock_list.call_args.kwargs == {"since": mod.FIRST_RELEASE}
    assert len(mock_fetch.call_args_list) == 3
    # Nothing stored: all of them.
    mock_fetch.reset_mock()
    mock_stored.return_value = {}
    mod.collect_wasde(bulk_backfill=True)
    assert [c.args[0] for c in mock_fetch.call_args_list] == sorted(d for d, _ in RELEASES)


def test_stored_releases_without_table(mock_settings: None) -> None:
    assert mod._stored_releases() == {}


@patch("src.collectors.usda_wasde._stored_releases", return_value={})
@patch("src.collectors.usda_wasde.write_raw", return_value=5)
@patch("src.collectors.usda_wasde.fetch_release")
@patch("src.collectors.usda_wasde.list_releases")
def test_collect_skips_late_copy_of_a_report(
    mock_list: MagicMock, mock_fetch: MagicMock, mock_write: MagicMock,
    mock_stored: MagicMock, mock_settings: None,
) -> None:
    # ESMIS lists December 2018's report (no. 584) again three days later.
    mock_list.return_value = [(date(2018, 12, 14), "copy"), (date(2018, 12, 11), "real")]
    mock_fetch.side_effect = lambda d, _u: parse_wasde_xml(XML, d)  # same report number
    assert mod.collect_wasde(bulk_backfill=True) == 5
    written = mock_write.call_args_list
    assert len(written) == 1
    assert written[0].args[1]["release_date"][0] == date(2018, 12, 11)
