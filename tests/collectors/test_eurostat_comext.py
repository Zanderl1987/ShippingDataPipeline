from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.eurostat_comext import parse_comext_records

SAMPLE_XML_EXPORT = """<?xml version="1.0" encoding="UTF-8"?>
<m:GenericData xmlns:m="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
    xmlns:g="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic">
<m:DataSet>
<g:Series>
<g:SeriesKey>
<g:Value id="product" value="TOTAL"/>
<g:Value id="partner" value="US"/>
<g:Value id="freq" value="A"/>
<g:Value id="reporter" value="DE"/>
<g:Value id="indicators" value="VALUE_IN_EUROS"/>
<g:Value id="flow" value="2"/>
</g:SeriesKey>
<g:Obs><g:ObsDimension value="2022"/><g:ObsValue value="155900289429"/></g:Obs>
<g:Obs><g:ObsDimension value="2023"/><g:ObsValue value="157726847016"/></g:Obs>
</g:Series>
<g:Series>
<g:SeriesKey>
<g:Value id="product" value="TOTAL"/>
<g:Value id="partner" value="FR"/>
<g:Value id="freq" value="A"/>
<g:Value id="reporter" value="DE"/>
<g:Value id="indicators" value="VALUE_IN_EUROS"/>
<g:Value id="flow" value="2"/>
</g:SeriesKey>
<g:Obs><g:ObsDimension value="2023"/><g:ObsValue value="105000000000"/></g:Obs>
</g:Series>
</m:DataSet>
</m:GenericData>
"""

SAMPLE_XML_IMPORT = """<?xml version="1.0" encoding="UTF-8"?>
<m:GenericData xmlns:m="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message"
    xmlns:g="http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic">
<m:DataSet>
<g:Series>
<g:SeriesKey>
<g:Value id="product" value="TOTAL"/>
<g:Value id="partner" value="US"/>
<g:Value id="freq" value="A"/>
<g:Value id="reporter" value="DE"/>
<g:Value id="indicators" value="VALUE_IN_EUROS"/>
<g:Value id="flow" value="1"/>
</g:SeriesKey>
<g:Obs><g:ObsDimension value="2023"/><g:ObsValue value="98000000000"/></g:Obs>
</g:Series>
</m:DataSet>
</m:GenericData>
"""


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


def test_parse_comext_records() -> None:
    df = parse_comext_records(SAMPLE_XML_EXPORT)
    assert df.height == 3
    by_key = {(r["partner_code"], r["year"]): r for r in df.to_dicts()}
    assert by_key[("US", 2022)]["trade_value_usd"] == pytest.approx(155900289429.0)
    assert by_key[("US", 2023)]["trade_value_usd"] == pytest.approx(157726847016.0)
    assert by_key[("FR", 2023)]["reporter_code"] == "DE"
    assert set(df["flow_code"].unique().to_list()) == {"X"}
    assert set(df["currency"].unique().to_list()) == {"EUR"}
    assert set(df["source"].unique().to_list()) == {"eurostat_comext"}
    assert "time_period" not in df.columns
    assert "trade_value_eur" not in df.columns


def test_parse_comext_records_import_flow() -> None:
    df = parse_comext_records(SAMPLE_XML_IMPORT)
    assert df.height == 1
    assert df["flow_code"][0] == "M"


def test_parse_comext_records_empty() -> None:
    assert parse_comext_records("<empty/>").height == 0


def test_parse_comext_records_invalid_xml() -> None:
    assert parse_comext_records("not xml").height == 0


@patch("src.collectors.eurostat_comext.write_raw")
@patch("src.collectors.eurostat_comext.fetch_comext_data")
def test_collect_comext_data(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.side_effect = [SAMPLE_XML_IMPORT, SAMPLE_XML_EXPORT]
    mock_write.return_value = 4

    from src.collectors.eurostat_comext import collect_comext_data

    count = collect_comext_data(reporter="DE")
    assert count == 4
    assert mock_fetch.call_count == 2
    mock_write.assert_called_once()
    written_df = mock_write.call_args[0][1]
    assert written_df.height == 4


@patch("src.collectors.eurostat_comext.write_raw")
@patch("src.collectors.eurostat_comext.fetch_comext_data")
def test_collect_comext_data_empty(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = "<empty/>"

    from src.collectors.eurostat_comext import collect_comext_data

    count = collect_comext_data(reporter="DE")
    assert count == 0
    mock_write.assert_not_called()
