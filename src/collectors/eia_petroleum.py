"""Collect US petroleum data from EIA."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
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
    data: str = "stoc/wstk",
    frequency: str = "weekly",
    facets: dict[str, list[str]] | None = None,
    length: int = 500,
) -> dict[str, Any]:
    """Fetch petroleum time-series data from EIA API v2.

    Args:
        data: v2 route under /petroleum (e.g. "stoc/wstk" for weekly stocks).
            Discover valid routes by GETting the parent path, e.g.
            https://api.eia.gov/v2/petroleum/stoc/?api_key=...
        frequency: "weekly", "monthly", or "annual". Valid values vary per
            route — pnp/unc is monthly/annual only, for instance.
        facets: Optional dict of facet filters (e.g. {"product": ["EPC0"], "du": ["NUS"]}).
        length: Max rows to return.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/{data}/data/"
    params: dict[str, Any] = {
        "api_key": _get_api_key(),
        "frequency": frequency,
        # v2 requires naming the column(s) to return, and rejects the v1-style
        # `units` parameter with HTTP 400. Units come back on each row instead.
        "data[0]": "value",
        "length": length,
        "sort[0][column]": "period",
        "sort[0][direction]": "desc",
    }

    if facets:
        for key, values in facets.items():
            for v in values:
                params[f"facets[{key}][]"] = v

    logger.info("Fetching EIA petroleum data: %s (%s)", data, frequency)
    resp = get_with_retry(url, params=params, timeout=60)
    result: dict[str, Any] = resp.json()
    return result


def get_weekly_stocks() -> dict[str, Any]:
    """Get weekly US crude oil and petroleum product stocks."""
    return get_petroleum_series(
        data="stoc/wstk",
        frequency="weekly",
        length=200,
    )


def get_weekly_supply() -> dict[str, Any]:
    """Get weekly US petroleum supply (production, refinery input, imports/exports)."""
    return get_petroleum_series(
        data="sum/sndw",
        frequency="weekly",
        length=200,
    )


def get_monthly_imports_by_country() -> dict[str, Any]:
    """Get monthly US crude oil imports by country of origin."""
    return get_petroleum_series(
        data="move/impcus",
        frequency="monthly",
        length=500,
    )


def get_refinery_utilization() -> dict[str, Any]:
    """Get weekly US refinery utilization rate."""
    return get_petroleum_series(
        data="pnp/wiup",
        frequency="weekly",
        length=200,
    )


def _normalize_period(period: str) -> str:
    """Coerce an EIA period to an ISO date the DATE column will accept.

    v2 returns "2026-07-24" for weekly data but "2026-04" for monthly, and a
    bare year-month will not cast to DATE.
    """
    if len(period) == 8 and period.isdigit():
        return f"{period[:4]}-{period[4:6]}-{period[6:8]}"
    if len(period) == 6 and period.isdigit():
        return f"{period[:4]}-{period[4:6]}-01"
    if len(period) == 7 and period[4] == "-":
        return f"{period}-01"
    return period


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
        # v2 rows are flat: product-name / area-name / duoarea, not the
        # "facets" and "area" lists this parser originally assumed.
        records.append({
            "period": row.get("period", ""),
            "product": row.get("product-name") or row.get("product", ""),
            "area": row.get("area-name", ""),
            "area_code": row.get("duoarea", ""),
            "value": row.get("value"),
            "unit": row.get("units") or default_unit,
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
        product = row.get("product-name") or row.get("product") or "unknown"
        area = row.get("area-name") or row.get("duoarea") or "US"
        report_date = _normalize_period(row.get("period", ""))

        records.append({
            "report_date": report_date,
            "product": product,
            "area": area,
            "area_code": row.get("duoarea", "") or area,
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
        product = row.get("product-name") or row.get("product") or "unknown"
        area = row.get("area-name") or row.get("duoarea") or "US"
        report_date = _normalize_period(row.get("period", ""))

        records.append({
            "report_date": report_date,
            "product": product,
            "area": area,
            "area_code": row.get("duoarea", "") or area,
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
