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

BASE_URL = "https://comtradeapi.un.org"

SOURCE = "un_comtrade"


def _get_api_key() -> str:
    """Get API key for UN Comtrade."""
    key = settings.un_comtrade_api_key
    if not key:
        raise ValueError("UN_COMTRADE_API_KEY not set in environment")
    return key


def get_trade_data(
    *,
    reporter_code: int,
    partner_code: int = 0,
    flow_code: str = "X",
    cmd_code: str = "TOTAL",
    period: str | None = None,
    max_records: int = 500,
) -> dict[str, Any]:
    """Get trade flow data.

    Args:
        reporter_code: Reporter country code (e.g. 156 for China).
        partner_code: Partner country code (0 = World).
        flow_code: Trade flow (X=Export, M=Import).
        cmd_code: Commodity code (TOTAL for all commodities).
        period: Year or month (e.g. "2025" or "202501").
        max_records: Max records to return.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/data/v1/get/C/A/HS"
    params: dict[str, Any] = {
        "reporterCode": reporter_code,
        "partnerCode": partner_code,
        "flowCode": flow_code,
        "cmdCode": cmd_code,
        "maxrecords": min(max_records, 100000),
        "subscription-key": _get_api_key(),
    }

    if period:
        params["period"] = period

    logger.info(
        "Fetching UN Comtrade data: reporter=%d, flow=%s", reporter_code, flow_code
    )
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def get_data_availability(
    reporter_code: int,
    period: str | None = None,
) -> dict[str, Any]:
    """Check data availability for a reporter.

    Args:
        reporter_code: Reporter country code.
        period: Optional year filter.

    Returns:
        Raw API response dict.
    """
    url = f"{BASE_URL}/data/v1/getDa"
    params: dict[str, Any] = {
        "reporterCode": reporter_code,
        "subscription-key": _get_api_key(),
    }

    if period:
        params["period"] = period

    logger.info("Checking UN Comtrade availability for reporter %d", reporter_code)
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _parse_trade_data(data: dict[str, Any]) -> pl.DataFrame:
    """Parse trade data into a DataFrame."""
    records_raw = data.get("data", [])
    if not records_raw:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for row in records_raw:
        records.append({
            "reporter_code": row.get("rtCode"),
            "partner_code": row.get("ptCode"),
            "commodity_code": str(row.get("cmdCode", "")),
            "flow_code": row.get("flowCode"),
            "year": row.get("period"),
            "trade_value_usd": row.get("primaryValue"),
            "net_weight_kg": row.get("netWgt"),
            "gross_weight_kg": row.get("grossWgt"),
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    for col in ["trade_value_usd", "net_weight_kg", "gross_weight_kg"]:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_trade_data(
    *,
    reporter_code: int,
    partner_code: int = 0,
    flow_code: str = "X",
    cmd_code: str = "TOTAL",
    period: str | None = None,
    max_records: int = 500,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect trade data and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = get_trade_data(
            reporter_code=reporter_code,
            partner_code=partner_code,
            flow_code=flow_code,
            cmd_code=cmd_code,
            period=period,
            max_records=max_records,
        )
        df = _parse_trade_data(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No trade data returned")
            return 0

        logger.info("Writing %d trade records from UN Comtrade", df.height)
        count = write_raw(SOURCE, df, table_name="trade_flow")
        tc.rows_written = count
        return count
