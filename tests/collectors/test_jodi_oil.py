from __future__ import annotations

from unittest.mock import MagicMock, patch

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


class TestGetData:
    @patch("src.collectors.jodi_oil.requests.get")
    def test_returns_csv(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.text = MOCK_PRIMARY_CSV
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_primary_data()
        assert "CRUDEOIL" in result

    @patch("src.collectors.jodi_oil.requests.get")
    def test_secondary_returns_csv(self, mock_get: MagicMock) -> None:
        mock_resp = MagicMock()
        mock_resp.text = MOCK_SECONDARY_CSV
        mock_resp.raise_for_status = MagicMock()
        mock_get.return_value = mock_resp

        result = get_secondary_data()
        assert "GASOLINE" in result

    @patch("src.collectors.jodi_oil.requests.get")
    def test_raises_on_error(self, mock_get: MagicMock) -> None:
        mock_get.side_effect = Exception("Download failed")
        with pytest.raises(Exception, match="Download failed"):
            get_primary_data()
