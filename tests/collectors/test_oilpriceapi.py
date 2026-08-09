from __future__ import annotations

import pytest

from src.collectors.oilpriceapi import (
    _extract_prices,
    _parse_freight,
    _parse_oil_prices,
    _price_to_date,
)


def make_price(code: str, price: float, updated: str = "2026-08-09T19:18:29Z") -> dict:
    return {"code": code, "name": code, "price": price, "currency": "USD", "updated_at": updated}


@pytest.fixture
def mock_prices() -> list[dict]:
    return [
        make_price("BRENT_CRUDE_USD", 82.21),
        make_price("WTI_USD", 78.55),
        make_price("DUBAI_CRUDE_USD", 80.1),
    ]


class TestExtractPrices:
    def test_list_shape(self) -> None:
        data = {"status": "success", "data": {"prices": [make_price("WTI_USD", 78.5)]}}
        prices = _extract_prices(data)
        assert len(prices) == 1
        assert prices[0]["code"] == "WTI_USD"

    def test_single_shape(self) -> None:
        data = {
            "status": "success",
            "data": {"price": 85.42, "formatted": "$85.42", "code": "BRENT_CRUDE_USD"},
        }
        prices = _extract_prices(data)
        assert len(prices) == 1
        assert prices[0]["price"] == 85.42

    def test_empty(self) -> None:
        assert _extract_prices({"data": {}}) == []


class TestParseOilPrices:
    def test_maps_benchmarks(self, mock_prices: list) -> None:
        df = _parse_oil_prices(mock_prices)
        assert df.height == 1
        assert df["brent_usd"][0] == pytest.approx(82.21, abs=1e-6)
        assert df["wti_usd"][0] == pytest.approx(78.55, abs=1e-6)
        assert df["dubai_usd"][0] == pytest.approx(80.1, abs=1e-6)
        assert df["source"][0] == "oilpriceapi"

    def test_unknown_code_dropped(self, mock_prices: list) -> None:
        df = _parse_oil_prices([*mock_prices, make_price("GOLD_USD", 2400.0)])
        assert df.height == 1

    def test_empty(self) -> None:
        assert _parse_oil_prices([]).height == 0


class TestParseFreight:
    def test_maps_indices(self) -> None:
        prices = [
            make_price("BALTIC_DRY_INDEX", 1750),
            make_price("SCFI", 1200),
        ]
        df = _parse_freight(prices)
        assert df.height == 2
        assert df["route_code"].to_list() == ["BALTIC_DRY_INDEX", "SCFI"]
        assert df["source"].to_list() == ["oilpriceapi", "oilpriceapi"]

    def test_missing_gated_code_tolerated(self) -> None:
        df = _parse_freight([make_price("NOT_A_CODE", 5.0)])
        assert df.height == 0

    def test_empty(self) -> None:
        assert _parse_freight([]).height == 0


class TestPriceToDate:
    def test_iso_date(self) -> None:
        assert _price_to_date(make_price("WTI_USD", 1)).isoformat() == "2026-08-09"

    def test_fallback_to_today(self) -> None:
        from datetime import date

        assert _price_to_date({}) == date.today()
