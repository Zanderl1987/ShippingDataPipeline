"""Collect US international trade flow data from the US Census Bureau.

The Census intltrade timeseries API
(``api.census.gov/data/timeseries/intltrade``) covers monthly US exports
and imports by Harmonized System (HS) code and trading partner country.
Every query now requires a free, instant API key
(``api.census.gov/data/key_signup.html``) -- Census tightened this at some
point; the API used to allow limited keyless access (probed 2026-08-28,
every query now returns ``{"error": "error: Missing Key"}``).

Field names differ between the two flows: exports use ``E_COMMODITY`` /
``ALL_VAL_MO``, imports use ``I_COMMODITY`` / ``GEN_VAL_MO`` (Census's
"general imports" total-value measure). Both were confirmed against the
endpoints' own keyless ``variables.json`` metadata and the API's published
example query (``exports/hs?get=E_COMMODITY_SDESC,CTY_NAME,ALL_VAL_YR,
DIST_NAME&time=2013-01&CTY_CODE=1220``), not guessed.

CAUTION: the metadata above is keyless and was live-verified, but the
*authenticated* JSON response body has NOT been -- CENSUS_API_KEY was not
yet registered as of this writing. The parser assumes Census's standard
list-of-lists response shape (header row first) used across every Census
Bureau API for well over a decade, but re-verify against a real response
the first time a key is available.
"""
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

BASE_URL = "https://api.census.gov/data/timeseries/intltrade"
SOURCE = "census_trade"

# Endpoint path, HS-code field, and value field differ between flows.
_FLOW_CONFIG = {
    "X": {  # exports
        "path": "exports/hs",
        "commodity_field": "E_COMMODITY",
        "value_field": "ALL_VAL_MO",
    },
    "M": {  # imports
        "path": "imports/hs",
        "commodity_field": "I_COMMODITY",
        "value_field": "GEN_VAL_MO",
    },
}


def _get_api_key() -> str:
    key = settings.census_api_key
    if not key:
        raise ValueError("CENSUS_API_KEY not set in environment")
    return key


def fetch_trade_data(
    *,
    flow_code: str,
    period: str,
    comm_lvl: str = "HS2",
    cty_code: str | None = None,
) -> list[list[str]]:
    """Fetch one month of US exports or imports by HS commodity and country.

    Args:
        flow_code: "X" for exports, "M" for imports.
        period: Month as "YYYY-MM".
        comm_lvl: HS aggregation level (HS2/HS4/HS6/HS10).
        cty_code: Optional single Census country code filter (4-char).

    Returns:
        Raw Census API response: a list of lists, header row first.
    """
    if flow_code not in _FLOW_CONFIG:
        raise ValueError(f"flow_code must be 'X' or 'M', got {flow_code!r}")
    cfg = _FLOW_CONFIG[flow_code]

    params: dict[str, Any] = {
        "get": f"{cfg['commodity_field']},CTY_CODE,CTY_NAME,{cfg['value_field']}",
        "COMM_LVL": comm_lvl,
        "time": period,
        "key": _get_api_key(),
    }
    if cty_code:
        params["CTY_CODE"] = cty_code

    url = f"{BASE_URL}/{cfg['path']}"
    logger.info(
        "Fetching Census trade data: flow=%s period=%s comm_lvl=%s",
        flow_code, period, comm_lvl,
    )
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    result: list[list[str]] = resp.json()
    return result


def parse_census_trade_data(
    rows: list[list[str]],
    flow_code: str,
    period: str,
) -> pl.DataFrame:
    """Parse Census's list-of-lists response into a trade_flow-shaped frame."""
    if not rows or len(rows) < 2:
        return pl.DataFrame()

    header = rows[0]
    cfg = _FLOW_CONFIG[flow_code]

    try:
        commodity_i = header.index(cfg["commodity_field"])
        cty_i = header.index("CTY_CODE")
        value_i = header.index(cfg["value_field"])
    except ValueError:
        logger.warning("Census response header missing expected columns: %s", header)
        return pl.DataFrame()

    year = int(period[:4])
    records: list[dict[str, Any]] = []
    for row in rows[1:]:
        try:
            value = float(row[value_i])
        except (TypeError, ValueError, IndexError):
            continue
        records.append({
            "reporter_code": "US",
            "partner_code": row[cty_i],
            "commodity_code": row[commodity_i],
            "flow_code": flow_code,
            "year": year,
            "trade_value_usd": value,
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records).with_columns(
        pl.lit("USD").alias("currency"),
        pl.lit(SOURCE).alias("source"),
    )
    return df


def collect_trade_data(
    *,
    period: str | None = None,
    comm_lvl: str = "HS2",
    flows: tuple[str, ...] = ("X", "M"),
    tracker: SourceTracker | None = None,
) -> int:
    """Collect US Census international trade data and write to storage.

    Defaults to 2 months before the current month -- Census intltrade
    figures typically lag a few weeks past month-end.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    if period is None:
        today = date.today()
        month = today.month - 2
        year = today.year
        if month <= 0:
            month += 12
            year -= 1
        period = f"{year:04d}-{month:02d}"

    with TimedCollector(tracker, SOURCE) as tc:
        frames: list[pl.DataFrame] = []
        for flow_code in flows:
            rows = fetch_trade_data(flow_code=flow_code, period=period, comm_lvl=comm_lvl)
            frame = parse_census_trade_data(rows, flow_code, period)
            if frame.height:
                frames.append(frame)

        df = pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Census trade data returned for period=%s", period)
            return 0

        logger.info("Writing %d Census trade records for period=%s", df.height, period)
        count = write_raw(SOURCE, df, table_name="trade_flow")
        tc.rows_written = count
        return count
