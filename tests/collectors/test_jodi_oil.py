from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

from src.collectors.jodi_oil import (
    _parse_jodi_csv,
    get_primary_data,
    get_secondary_data,
)

MOCK_PRIMARY_CSV = (
    "REPORTING_COUNTRY,PARTNER_COUNTRY,PRODUCT,"
    "FLOW_BREAKDOWN,DATAVALUE,UNIT,TIME_PERIOD\n"
    "USA,World,CRUDEOIL,INDPROD,8500.5,KTONS,2024-01\n"
    "USA,World,CRUDEOIL,TOTIMPSB,3200.3,KTONS,2024-01\n"
    "USA,World,CRUDEOIL,TOTEXPSB,1100.2,KTONS,2024-01\n"
    "USA,World,NGL,INDPROD,2800.1,KTONS,2024-01\n"
)

MOCK_SECONDARY_CSV = (
    "REPORTING_COUNTRY,PARTNER_COUNTRY,PRODUCT,"
    "FLOW_BREAKDOWN,DATAVALUE,UNIT,TIME_PERIOD\n"
    "USA,World,GASOLINE,TOTDEMO,12500.4,KBBL,2024-01\n"
    "USA,World,GASDIES,TOTDEMO,4800.2,KBBL,2024-01\n"
    "USA,World,JETKERO,TOTDEMO,1600.3,KBBL,2024-01\n"
)


# JODI's current column names, with its placeholder values for
# not-available ("-"), confidential ("x") and "N/A".
MOCK_NEW_SCHEMA_CSV = (
    "REF_AREA,TIME_PERIOD,ENERGY_PRODUCT,FLOW_BREAKDOWN,"
    "UNIT_MEASURE,OBS_VALUE,ASSESSMENT_CODE\n"
    "AE,2024-01,CRUDEOIL,INDPROD,KTONS,7596.0,3\n"
    "AE,2024-01,CRUDEOIL,CLOSTLV,KTONS,-,3\n"
    "AE,2024-01,CRUDEOIL,TOTEXPSB,KTONS,x,3\n"
    "AE,2024-01,CRUDEOIL,TOTIMPSB,KBBL,1234.5,3\n"
    "AE,2024-01,CRUDEOIL,INDPROD,KBD,999.0,3\n"
    "AE,2024-01,CRUDEOIL,INDPROD,KL,888.0,3\n"
)


class TestParseCurrentSchema:
    def test_parses_renamed_columns(self) -> None:
        df = _parse_jodi_csv(MOCK_NEW_SCHEMA_CSV)
        assert df["reporting_country"][0] == "AE"
        assert df["product_code"][0] == "CRUDEOIL"
        assert df["period"][0] == "2024-01"

    def test_skips_units_with_no_column(self) -> None:
        # KBD (a rate) and KL (kilolitres) map to neither quantity column.
        df = _parse_jodi_csv(MOCK_NEW_SCHEMA_CSV)
        assert set(df["unit"].to_list()) == {"KTONS", "KBBL"}

    def test_drops_placeholder_rows(self) -> None:
        # "-" (not available) and "x" (confidential) are not measurements, and
        # must never be stored as zero.
        df = _parse_jodi_csv(MOCK_NEW_SCHEMA_CSV)
        assert df.height == 2
        assert df.filter(pl.col("flow") == "Closing stocks").height == 0
        assert df.filter(pl.col("flow") == "Exports").height == 0
        assert 0.0 not in df["quantity_ktonnes"].to_list()

    def test_routes_value_to_correct_unit_column(self) -> None:
        df = _parse_jodi_csv(MOCK_NEW_SCHEMA_CSV)
        mass = df.filter((pl.col("unit") == "KTONS") & (pl.col("flow") == "Production"))
        assert mass["quantity_ktonnes"][0] == 7596.0
        assert mass["quantity_barrels"][0] is None
        vol = df.filter(pl.col("unit") == "KBBL")
        assert vol["quantity_barrels"][0] == 1234.5
        assert vol["quantity_ktonnes"][0] is None


class TestParseJodiCsv:
    def test_parses_primary(self) -> None:
        df = _parse_jodi_csv(MOCK_PRIMARY_CSV)
        assert df.height == 4
        assert "period" in df.columns
        assert "reporting_country" in df.columns
        assert "product" in df.columns
        assert "flow" in df.columns
        assert "source" in df.columns
        assert "partition_date" in df.columns

    def test_product_names_resolved(self) -> None:
        df = _parse_jodi_csv(MOCK_PRIMARY_CSV)
        products = df["product"].to_list()
        assert "Crude oil" in products
        assert "NGL" in products

    def test_flow_names_resolved(self) -> None:
        df = _parse_jodi_csv(MOCK_PRIMARY_CSV)
        flows = df["flow"].to_list()
        assert "Production" in flows
        assert "Imports" in flows
        assert "Exports" in flows

    def test_filter_by_product(self) -> None:
        df = _parse_jodi_csv(MOCK_PRIMARY_CSV, products=["CRUDEOIL"])
        assert df.height == 3

    def test_parses_secondary(self) -> None:
        df = _parse_jodi_csv(MOCK_SECONDARY_CSV)
        assert df.height == 3
        products = df["product"].to_list()
        assert "Motor/aviation gasoline" in products

    def test_empty_csv(self) -> None:
        hdr = "REPORTING_COUNTRY,PARTNER_COUNTRY,PRODUCT,"
        hdr += "FLOW_BREAKDOWN,DATAVALUE,UNIT,TIME_PERIOD\n"
        df = _parse_jodi_csv(hdr)
        assert df.height == 0

    def test_source_column(self) -> None:
        df = _parse_jodi_csv(MOCK_PRIMARY_CSV)
        assert df["source"][0] == "jodi_oil"


def _as_zip(csv_text: str, member: str = "NewProcedure_Primary_CSV.csv") -> bytes:
    """Pack CSV text into a zip archive, as JODI now serves it."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(member, csv_text)
    return buf.getvalue()


class TestGetData:
    @patch("src.collectors.jodi_oil.requests.get")
    def test_returns_csv(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.content = _as_zip(MOCK_PRIMARY_CSV)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_primary_data()
        assert "CRUDEOIL" in result

    @patch("src.collectors.jodi_oil.requests.get")
    def test_secondary_returns_csv(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.content = _as_zip(MOCK_SECONDARY_CSV)
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_secondary_data()
        assert "GASOLINE" in result

    @patch("src.collectors.jodi_oil.requests.get")
    def test_raises_when_archive_has_no_csv(self, mock_get: MagicMock) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "no data here")
        mock_resp = MagicMock()
        mock_resp.content = buf.getvalue()
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        with pytest.raises(ValueError, match="No CSV member"):
            get_primary_data()

    @patch("src.collectors.jodi_oil.requests.get")
    def test_raises_on_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("Download failed")
        with pytest.raises(Exception, match="Download failed"):
            get_primary_data()
