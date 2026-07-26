from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://api.eia.gov/v2/petroleum"
SOURCE = "eia_petroleum"


def _get_api_key() -> str:
    key = settings.eia_api_key
    if not key:
        raise ValueError("EIA_API_KEY not set in environment")
    return key


def get_petroleum_series(
    *,
    data: str = "pet-st",
    frequency: str = "weekly",
    units: str = "MBBL",
    facets: dict[str, list[str]] | None = None,
    length: int = 500,
) -> dict[str, Any]:
    """Fetch petroleum time-series data from EIA API v2.

    Args:
        data: Dataset path (e.g. "pet-st" for stocks, "pet-pnp-inpt" for refinery input).
        frequency: "weekly", "monthly", or "annual".
        units: Unit code (MBBL = thousand barrels, etc.).
        facets: Optional dict of facet filters (e.g. {"product": ["EPC0"], "du": ["NUS"]}).
        length: Max rows to return.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/{data}/data/"
    params: dict[str, Any] = {
        "api_key": _get_api_key(),
        "frequency": frequency,
        "units": units,
        "length": length,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
    }

    if facets:
        for key, values in facets.items():
            for v in values:
                params[f"facets[{key}][]"] = v

    logger.info("Fetching EIA petroleum data: %s (%s)", data, frequency)
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_weekly_stocks() -> dict[str, Any]:
    """Get weekly US crude oil and petroleum product stocks."""
    return get_petroleum_series(
        data="pet-st",
        frequency="weekly",
        units="MBBL",
        length=200,
    )


def get_weekly_supply() -> dict[str, Any]:
    """Get weekly US petroleum supply (production, refinery input, imports/exports)."""
    return get_petroleum_series(
        data="pet-wdi",
        frequency="weekly",
        units="MBBL/D",
        length=200,
    )


def get_monthly_imports_by_country() -> dict[str, Any]:
    """Get monthly US crude oil imports by country of origin."""
    return get_petroleum_series(
        data="pet-mcr-impt-nus-pt2-d",
        frequency="monthly",
        units="MBBL",
        length=500,
    )


def get_refinery_utilization() -> dict[str, Any]:
    """Get weekly US refinery utilization rate."""
    return get_petroleum_series(
        data="pet-wiup",
        frequency="weekly",
        units="PCT",
        length=200,
    )


def _parse_eia_response(
    data: dict[str, Any],
    frequency: str = "weekly",
    default_unit: str = "MBBL",
) -> pl.DataFrame:
    """Parse EIA API v2 response into a DataFrame."""
    response_data = data.get("response", {})
    records_raw = response_data.get("data", [])

    if not records_raw:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for row in records_raw:
        product_name = ""
        facets = row.get("facets", [])
        if facets:
            product_name = facets[0] if isinstance(facets, list) and facets else ""

        area = row.get("area", [])
        area_name = ""
        area_code = ""
        if isinstance(area, list) and area:
            area_name = area[0]
            area_code = area[0]
        elif isinstance(area, str):
            area_name = area
            area_code = area

        records.append({
            "period": row.get("period", ""),
            "product": product_name,
            "area": area_name,
            "area_code": area_code,
            "value": row.get("value"),
            "unit": row.get("units", default_unit),
            "frequency": frequency,
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    if "value" in df.columns:
        df = df.with_columns(pl.col("value").cast(pl.Float64, strict=False))

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def _parse_eia_supply_response(
    data: dict[str, Any],
    frequency: str = "weekly",
    default_unit: str = "MBBL/D",
) -> pl.DataFrame:
    """Parse EIA API v2 response into oil_inventories schema."""
    response_data = data.get("response", {})
    records_raw = response_data.get("data", [])

    if not records_raw:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for row in records_raw:
        product = row.get("product", ["unknown"])
        if isinstance(product, list) and product:
            product = product[0]

        area = row.get("area", ["US"])
        if isinstance(area, list) and area:
            area = area[0]
        elif isinstance(area, str):
            pass
        else:
            area = "US"

        period = row.get("period", "")
        report_date = period
        if len(period) == 8 and period.isdigit():
            report_date = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
        elif len(period) == 6 and period.isdigit():
            report_date = f"{period[:4]}-{period[4:6]}-01"

        records.append({
            "report_date": report_date,
            "product": product,
            "area": area,
            "area_code": area,
            "stock_type": "supply",
            "value_thousand_bbl": row.get("value"),
            "unit": row.get("units", default_unit),
            "frequency": frequency,
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    if "value_thousand_bbl" in df.columns:
        df = df.with_columns(pl.col("value_thousand_bbl").cast(pl.Float64, strict=False))

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def _parse_eia_stocks_response(data: dict[str, Any]) -> pl.DataFrame:
    """Parse EIA weekly stocks response into oil_inventories schema."""
    response_data = data.get("response", {})
    records_raw = response_data.get("data", [])

    if not records_raw:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for row in records_raw:
        product = row.get("product", ["unknown"])
        if isinstance(product, list) and product:
            product = product[0]

        area = row.get("du", ["US"])
        if isinstance(area, list) and area:
            area = area[0]

        period = row.get("period", "")
        report_date = period
        if len(period) == 8 and period.isdigit():
            report_date = f"{period[:4]}-{period[4:6]}-{period[6:8]}"
        elif len(period) == 6 and period.isdigit():
            report_date = f"{period[:4]}-{period[4:6]}-01"

        records.append({
            "report_date": report_date,
            "product": product,
            "area": area,
            "area_code": area,
            "stock_type": "commercial",
            "value_thousand_bbl": row.get("value"),
            "unit": row.get("units", "MBBL"),
            "frequency": "weekly",
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    if "value_thousand_bbl" in df.columns:
        df = df.with_columns(pl.col("value_thousand_bbl").cast(pl.Float64, strict=False))

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_weekly_stocks(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect weekly US petroleum stocks and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_weekly_stocks()
        df = _parse_eia_stocks_response(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No EIA stock data returned")
            return 0

        logger.info("Writing %d EIA stock records", df.height)
        count = write_raw(SOURCE, df, table_name="oil_inventories")
        tc.rows_written = count
        return count


def collect_weekly_supply(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect weekly US petroleum supply data.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_weekly_supply()
        df = _parse_eia_supply_response(raw, frequency="weekly", default_unit="MBBL/D")
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No EIA supply data returned")
            return 0

        logger.info("Writing %d EIA supply records", df.height)
        count = write_raw(SOURCE, df, table_name="oil_inventories")
        tc.rows_written = count
        return count


def collect_monthly_imports(
    tracker: SourceTracker | None = None,
) -> int:
    """Collect monthly US crude oil imports by country.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_monthly_imports_by_country()
        df = _parse_eia_supply_response(raw, frequency="monthly", default_unit="MBBL")
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No EIA import data returned")
            return 0

        logger.info("Writing %d EIA import records", df.height)
        count = write_raw(SOURCE, df, table_name="oil_inventories")
        tc.rows_written = count
        return count
