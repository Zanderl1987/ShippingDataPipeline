"""Collect monthly US imports and exports from the US Census Bureau.

The Census intltrade timeseries API
(``api.census.gov/data/timeseries/intltrade``) covers monthly US trade from
2013 by Harmonized System (HS) code and partner country. Every query needs a
free API key (``api.census.gov/data/key_signup.html``). A missing or
unactivated key is answered with a 302 to ``missing_key.html`` /
``invalid_key.html`` rather than a 4xx, so redirects are not followed.

Two tables are written:

``us_trade_products``
    Every HS10 product, world total, per month. Quantities exist only at
    HS10 -- at HS6 and above the unit field is blank (live-verified
    2026-09-30) -- so this is the grain that allows unit values
    (value / quantity), which separate price changes from volume changes.
    Imports carry general and consumption value and quantity, duty, CIF and
    charges. Exports are fetched twice, domestic (DF=1) and foreign-origin
    re-exports (DF=2), so re-exports can be removed from US supply. Both
    flows carry value and shipping weight by vessel, air and container.

``us_trade_partners``
    HS2 chapters by individual partner country, per month. The API's
    country list also carries the world total (``-``) and regional groups
    (``0003`` EU, ``0020`` USMCA, ``0022`` OECD ...) that overlap the
    countries, as do continents (``1XXX`` North America ... ``7XXX`` Africa);
    those are dropped so rows sum without double counting (July 2026 imports
    by country sum to the $332.9B world total).

Census lags about five weeks past month-end, revises the prior month at
each release, and publishes an annual revision in June covering the prior
three years. Each run re-fetches the last few months, and after the June
revision a longer window, so revised values replace first prints.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import duckdb
import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import get_connection, write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://api.census.gov/data/timeseries/intltrade"
SOURCE = "census_trade"
PRODUCTS_TABLE = "us_trade_products"
PARTNERS_TABLE = "us_trade_partners"

#: First month the timeseries API serves.
FIRST_PERIOD = (2013, 1)
#: Months re-fetched on a normal run (covers the prior-month revision).
RECENT_MONTHS = 4
#: Months re-fetched in July/August, after the June annual revision.
ANNUAL_REVISION_MONTHS = 40

REQUEST_TIMEOUT = 300
MAX_ATTEMPTS = 3
RETRY_BACKOFF_SECONDS = 20
#: Months per products request (a year of HS10 imports is ~11 MB, ~30 s).
PRODUCT_CHUNK_MONTHS = 12
#: Months per chapter-by-country range (all history of ch. 84 took ~21 s).
PARTNER_CHUNK_MONTHS = 24
#: Seconds per run spent filling older gaps (CI's whole job is capped at 90 min).
BACKFILL_BUDGET_SECONDS = 30 * 60
#: HS2 chapters 01-99; 77 is reserved and carries no trade.
HS2_CHAPTERS = [f"{c:02d}" for c in range(1, 100) if c != 77]

# API variable -> output column. Monthly ("_MO") measures only.
_IMPORT_PRODUCT_VARS = {
    "I_COMMODITY": "commodity_code",
    "I_COMMODITY_SDESC": "commodity_desc",
    "GEN_VAL_MO": "value_usd",
    "CON_VAL_MO": "consumption_value_usd",
    "GEN_QY1_MO": "quantity_1",
    "GEN_QY2_MO": "quantity_2",
    "CON_QY1_MO": "consumption_quantity_1",
    "CON_QY2_MO": "consumption_quantity_2",
    "UNIT_QY1": "unit_1",
    "UNIT_QY2": "unit_2",
    "DUT_VAL_MO": "dutiable_value_usd",
    "CAL_DUT_MO": "calculated_duty_usd",
    "GEN_CIF_MO": "cif_value_usd",
    "GEN_CHA_MO": "charges_usd",
    "VES_VAL_MO": "vessel_value_usd",
    "VES_WGT_MO": "vessel_weight_kg",
    "AIR_VAL_MO": "air_value_usd",
    "AIR_WGT_MO": "air_weight_kg",
    "CNT_VAL_MO": "container_value_usd",
    "CNT_WGT_MO": "container_weight_kg",
}

_EXPORT_PRODUCT_VARS = {
    "E_COMMODITY": "commodity_code",
    "E_COMMODITY_SDESC": "commodity_desc",
    "ALL_VAL_MO": "value_usd",
    "QTY_1_MO": "quantity_1",
    "QTY_2_MO": "quantity_2",
    "UNIT_QY1": "unit_1",
    "UNIT_QY2": "unit_2",
    "VES_VAL_MO": "vessel_value_usd",
    "VES_WGT_MO": "vessel_weight_kg",
    "AIR_VAL_MO": "air_value_usd",
    "AIR_WGT_MO": "air_weight_kg",
    "CNT_VAL_MO": "container_value_usd",
    "CNT_WGT_MO": "container_weight_kg",
}

_IMPORT_PARTNER_VARS = {
    "I_COMMODITY": "commodity_code",
    "I_COMMODITY_SDESC": "commodity_desc",
    "CTY_CODE": "partner_code",
    "CTY_NAME": "partner_name",
    "GEN_VAL_MO": "value_usd",
    "CON_VAL_MO": "consumption_value_usd",
    "CAL_DUT_MO": "calculated_duty_usd",
    "VES_VAL_MO": "vessel_value_usd",
    "VES_WGT_MO": "vessel_weight_kg",
    "AIR_VAL_MO": "air_value_usd",
    "AIR_WGT_MO": "air_weight_kg",
    "CNT_VAL_MO": "container_value_usd",
    "CNT_WGT_MO": "container_weight_kg",
}

_EXPORT_PARTNER_VARS = {
    "E_COMMODITY": "commodity_code",
    "E_COMMODITY_SDESC": "commodity_desc",
    "CTY_CODE": "partner_code",
    "CTY_NAME": "partner_name",
    "ALL_VAL_MO": "value_usd",
    "VES_VAL_MO": "vessel_value_usd",
    "VES_WGT_MO": "vessel_weight_kg",
    "AIR_VAL_MO": "air_value_usd",
    "AIR_WGT_MO": "air_weight_kg",
    "CNT_VAL_MO": "container_value_usd",
    "CNT_WGT_MO": "container_weight_kg",
}

_TEXT_COLUMNS = {
    "commodity_code", "commodity_desc", "unit_1", "unit_2",
    "partner_code", "partner_name",
}

#: DF predicate values for exports.
EXPORT_ORIGINS = {"1": "domestic", "2": "foreign"}


def _get_api_key() -> str:
    key = settings.census_api_key
    if not key:
        raise ValueError("CENSUS_API_KEY not set in environment")
    return key


def fetch_census(path: str, params: dict[str, Any]) -> list[list[str]]:
    """GET one Census intltrade query; returns rows with the header first.

    An empty list means Census has no data for the query (HTTP 204), which
    is how a not-yet-released month answers.
    """
    query = {**params, "key": _get_api_key()}
    # Errors name the query without the key: requests' own messages embed
    # the full URL, key included, and CI logs are kept.
    described = f"{path} {params}"
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(
                f"{BASE_URL}/{path}", params=query, timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            )
        except requests.RequestException as exc:
            failure = f"{type(exc).__name__}"
        else:
            if resp.status_code in (301, 302):
                raise ValueError(
                    f"Census rejected the API key (redirect to "
                    f"{resp.headers.get('Location')}); check CENSUS_API_KEY and "
                    "that the key was activated"
                )
            if resp.status_code == 204:
                return []
            if resp.status_code == 200:
                result: list[list[str]] = resp.json()
                return result
            if resp.status_code < 500 and resp.status_code != 429:
                raise RuntimeError(
                    f"Census HTTP {resp.status_code} for {described}: {resp.text[:200]}"
                )
            failure = f"HTTP {resp.status_code}"
        logger.warning(
            "Census %s for %s (attempt %d/%d)", failure, described, attempt, MAX_ATTEMPTS,
        )
        if attempt < MAX_ATTEMPTS:
            time.sleep(RETRY_BACKOFF_SECONDS * attempt)
    raise RuntimeError(f"Census query failed after {MAX_ATTEMPTS} attempts: {described}")


def parse_census_rows(
    rows: list[list[str]],
    var_map: dict[str, str],
) -> pl.DataFrame:
    """Turn a Census list-of-lists response into typed, renamed columns.

    Each row's month comes from the ``time`` column Census appends, so one
    response can span a date range. Census also echoes predicate fields
    (e.g. ``I_COMMODITY``) a second time; the first occurrence wins.
    """
    if not rows or len(rows) < 2:
        return pl.DataFrame()

    header = rows[0]
    index: dict[str, int] = {}
    for i, name in enumerate(header):
        index.setdefault(name, i)
    missing = [v for v in [*var_map, "time"] if v not in index]
    if missing:
        logger.warning("Census response missing expected columns %s", missing)
        return pl.DataFrame()

    columns: dict[str, list[Any]] = {out: [] for out in var_map.values()}
    periods: list[str] = []
    for row in rows[1:]:
        periods.append(row[index["time"]])
        for var, out in var_map.items():
            raw = row[index[var]] if index[var] < len(row) else None
            if out in _TEXT_COLUMNS:
                columns[out].append(None if raw in (None, "", "-") else raw)
            else:
                try:
                    columns[out].append(float(raw))  # type: ignore[arg-type]
                except (TypeError, ValueError):
                    columns[out].append(None)

    schema = {
        out: (pl.Utf8 if out in _TEXT_COLUMNS else pl.Float64) for out in columns
    }
    period_date = pl.Series("period_date", periods).str.to_date("%Y-%m")
    return pl.DataFrame(columns, schema=schema).with_columns(
        period_date,
        period_date.dt.year().cast(pl.Int32).alias("year"),
        period_date.dt.month().cast(pl.Int32).alias("month"),
        pl.lit(SOURCE).alias("source"),
    )


def _time_range(start: str, end: str) -> str:
    return start if start == end else f"from {start} to {end}"


def fetch_products(start: str, end: str) -> pl.DataFrame:
    """All HS10 products, world total, both flows, for months start..end."""
    frames: list[pl.DataFrame] = []

    rows = fetch_census("imports/hs", {
        "get": ",".join(_IMPORT_PRODUCT_VARS),
        "time": _time_range(start, end),
        "COMM_LVL": "HS10",
        "CTY_CODE": "-",
    })
    imports = parse_census_rows(rows, _IMPORT_PRODUCT_VARS)
    if imports.height:
        frames.append(imports.with_columns(
            pl.lit("M").alias("flow_code"), pl.lit("all").alias("export_origin"),
        ))

    for df_code, origin in EXPORT_ORIGINS.items():
        rows = fetch_census("exports/hs", {
            "get": ",".join(_EXPORT_PRODUCT_VARS),
            "time": _time_range(start, end),
            "COMM_LVL": "HS10",
            "CTY_CODE": "-",
            "DF": df_code,
        })
        exports = parse_census_rows(rows, _EXPORT_PRODUCT_VARS)
        if exports.height:
            frames.append(exports.with_columns(
                pl.lit("X").alias("flow_code"), pl.lit(origin).alias("export_origin"),
            ))

    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def _is_country(code: str | None) -> bool:
    """True for an individual country, False for the world total or a group.

    Countries are four digits not starting with 0. The world total is ``-``,
    trade groups are ``0003``-``0028`` (EU, USMCA, OECD ...) and continents
    are ``1XXX``-``7XXX``, all overlapping the countries.
    """
    return code is not None and len(code) == 4 and code.isdigit() and code[0] != "0"


def fetch_partners(start: str, end: str, chapter: str) -> pl.DataFrame:
    """One HS2 chapter by individual partner country, both flows, start..end.

    Queried one chapter at a time: every chapter by every country in one
    request runs into Census's ~150 s server timeout (exports 500 every
    time, imports take ~135 s), while one chapter takes seconds even across
    the whole history (live-measured 2026-09-30).
    """
    frames: list[pl.DataFrame] = []
    for flow_code, path, var_map, field in (
        ("M", "imports/hs", _IMPORT_PARTNER_VARS, "I_COMMODITY"),
        ("X", "exports/hs", _EXPORT_PARTNER_VARS, "E_COMMODITY"),
    ):
        rows = fetch_census(path, {
            "get": ",".join(var_map),
            "time": _time_range(start, end),
            "COMM_LVL": "HS2",
            field: chapter,
        })
        df = parse_census_rows(rows, var_map)
        if df.height:
            df = df.filter(
                pl.col("partner_code").map_elements(_is_country, return_dtype=pl.Boolean)
            )
            frames.append(df.with_columns(pl.lit(flow_code).alias("flow_code")))
    return pl.concat(frames, how="diagonal_relaxed") if frames else pl.DataFrame()


def _months_back(end: tuple[int, int], count: int) -> list[str]:
    year, month = end
    periods: list[str] = []
    for _ in range(count):
        if (year, month) < FIRST_PERIOD:
            break
        periods.append(f"{year:04d}-{month:02d}")
        month -= 1
        if month == 0:
            year, month = year - 1, 12
    return list(reversed(periods))


def _last_month(today: date) -> tuple[int, int]:
    return (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)


def recent_periods(today: date) -> list[str]:
    """Months every run re-fetches, oldest first, ending last month.

    Last month is usually not released yet; Census returns no rows for it
    and a later run picks it up.
    """
    count = ANNUAL_REVISION_MONTHS if today.month in (7, 8) else RECENT_MONTHS
    return _months_back(_last_month(today), count)


def all_periods(today: date) -> list[str]:
    end = _last_month(today)
    count = (end[0] - FIRST_PERIOD[0]) * 12 + end[1] - FIRST_PERIOD[1] + 1
    return _months_back(end, count)


def _next_month(period: str) -> str:
    year, month = int(period[:4]), int(period[5:7]) + 1
    if month == 13:
        year, month = year + 1, 1
    return f"{year:04d}-{month:02d}"


def _chunks(periods: list[str], size: int) -> list[tuple[str, str]]:
    """Split sorted months into (start, end) ranges of consecutive months."""
    runs: list[list[str]] = []
    for period in periods:
        if runs and len(runs[-1]) < size and period == _next_month(runs[-1][-1]):
            runs[-1].append(period)
        else:
            runs.append([period])
    return [(run[0], run[-1]) for run in runs]


def _stored_periods(table: str) -> set[str]:
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT DISTINCT strftime(period_date, '%Y-%m') FROM {table} WHERE source = ?",
            [SOURCE],
        ).fetchall()
    except duckdb.CatalogException:
        return set()
    finally:
        conn.close()
    return {r[0] for r in rows}


def _collect_products(
    ranges: list[tuple[str, str]], tc: TimedCollector, deadline: float | None = None,
) -> int:
    written = 0
    for start, end in ranges:
        if deadline is not None and time.monotonic() > deadline:
            logger.info("Census products: time budget used, stopping before %s", start)
            break
        products = fetch_products(start, end)
        tc.rows_fetched += products.height
        if products.height:
            written += write_raw(f"{SOURCE}_products", products, table_name=PRODUCTS_TABLE)
        logger.info("Census products %s..%s: %d rows", start, end, products.height)
    return written


def _collect_partners(
    ranges: list[tuple[str, str]], tc: TimedCollector, deadline: float | None = None,
) -> int:
    written = 0
    for start, end in ranges:
        if deadline is not None and time.monotonic() > deadline:
            logger.info("Census partners: time budget used, stopping before %s", start)
            break
        # One write per range, after every chapter is in: a month counts as
        # stored once any row exists, so a half-written range would never be
        # filled in by the backfill.
        frames = [fetch_partners(start, end, chapter) for chapter in HS2_CHAPTERS]
        partners = pl.concat([f for f in frames if f.height], how="diagonal_relaxed") \
            if any(f.height for f in frames) else pl.DataFrame()
        tc.rows_fetched += partners.height
        if partners.height:
            written += write_raw(f"{SOURCE}_partners", partners, table_name=PARTNERS_TABLE)
        logger.info("Census partners %s..%s: %d rows", start, end, partners.height)
    return written


def collect_trade_data(
    *,
    periods: list[str] | None = None,
    tracker: SourceTracker | None = None,
    bulk_backfill: bool | None = None,
) -> int:
    """Collect Census monthly trade into us_trade_products and us_trade_partners.

    ``periods`` (sorted "YYYY-MM" months) fetches exactly those months. With
    none given, the recent revision window is re-fetched, and when bulk
    backfill is allowed (CI) months since 2013 still missing from each table
    are filled in, within ``BACKFILL_BUDGET_SECONDS`` per run -- the full
    history takes a few daily runs, which keeps each under the CI job limit.

    Returns:
        Number of rows written across both tables.
    """
    _get_api_key()
    if tracker is None:
        tracker = SourceTracker()
    if bulk_backfill is None:
        bulk_backfill = settings.allow_bulk_backfill

    product_gaps: list[str] = []
    partner_gaps: list[str] = []
    if periods is not None:
        recent = periods
    else:
        today = date.today()
        recent = recent_periods(today)
        if bulk_backfill:
            history = [p for p in all_periods(today) if p not in recent]
            stored = _stored_periods(PRODUCTS_TABLE)
            product_gaps = [p for p in history if p not in stored]
            stored = _stored_periods(PARTNERS_TABLE)
            partner_gaps = [p for p in history if p not in stored]

    with TimedCollector(tracker, SOURCE) as tc:
        # The recent window always runs in full; only filling older gaps is
        # held to the time budget, newest gap first.
        written = _collect_products(_chunks(recent, PRODUCT_CHUNK_MONTHS), tc)
        written += _collect_partners(_chunks(recent, PARTNER_CHUNK_MONTHS), tc)
        deadline = time.monotonic() + BACKFILL_BUDGET_SECONDS
        written += _collect_products(
            _chunks(product_gaps, PRODUCT_CHUNK_MONTHS)[::-1], tc, deadline,
        )
        written += _collect_partners(
            _chunks(partner_gaps, PARTNER_CHUNK_MONTHS)[::-1], tc, deadline,
        )
        tc.rows_written = written
        return written
