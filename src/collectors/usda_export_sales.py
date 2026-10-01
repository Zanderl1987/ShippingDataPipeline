"""Collect USDA weekly export sales (corn, soybeans, wheat) by destination.

USDA FAS's Export Sales Reporting program is the closest thing to an order
book for US exports: besides what shipped that week, it reports sales booked
but not yet shipped ("outstanding sales") for the current and next marketing
year. USDA's AgTransport Socrata portal mirrors the corn, soybean and wheat
series keylessly as dataset ``wnn7-29tu`` (1999-06 onward, live-verified
2026-09-30); the full 40-commodity program is only on FAS's own query system.

Reporting weeks run Friday-Thursday and are published the following
Thursday, so ``week_ending`` is a Thursday. Wheat rows are split by class
(``wheat_class``: HRW, HRS, SRW, White, Durum); corn and soybeans have none.
All quantities are metric tons.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

import duckdb
import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import get_connection, write_raw

logger = logging.getLogger(__name__)

ENDPOINT = "https://agtransport.usda.gov/resource/wnn7-29tu.json"
SOURCE = "usda_export_sales"
TABLE = "us_export_sales"
PAGE_SIZE = 50_000
#: Weeks re-fetched on a normal run; late-filed corrections land within this.
RECENT_WEEKS = 8

# Socrata field -> output column. "cmy"/"nmy" = current/next marketing year.
_NUMERIC_FIELDS = {
    "wkexportscmy": "weekly_exports",
    "accexportscmy": "accumulated_exports",
    "outsalescmy": "outstanding_sales",
    "grosalescmy": "gross_new_sales",
    "netsalescmy": "net_sales",
    "totcommcmy": "total_commitments",
    "outsalesnmy": "next_my_outstanding_sales",
    "netsalesnmy": "next_my_net_sales",
}


def fetch_export_sales(since: date | None) -> list[dict[str, Any]]:
    """All rows with week ending on/after ``since`` (everything if None)."""
    rows: list[dict[str, Any]] = []
    offset = 0
    while True:
        params: dict[str, Any] = {
            "$limit": PAGE_SIZE,
            "$offset": offset,
            "$order": ":id",
        }
        if since is not None:
            params["$where"] = f"date >= '{since.isoformat()}T00:00:00.000'"
        resp = requests.get(ENDPOINT, params=params, timeout=120)
        resp.raise_for_status()
        page: list[dict[str, Any]] = resp.json()
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        offset += PAGE_SIZE


def parse_export_sales(rows: list[dict[str, Any]]) -> pl.DataFrame:
    """Normalize Socrata rows into ``us_export_sales`` columns."""
    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            week_ending = date.fromisoformat(row["date"][:10])
        except (KeyError, TypeError, ValueError):
            continue
        record: dict[str, Any] = {
            "week_ending": week_ending,
            "marketing_year": row.get("myear"),
            "commodity": row.get("commodity"),
            "wheat_class": row.get("type"),
            "country": row.get("country"),
            "unit": row.get("unit"),
        }
        for field, out in _NUMERIC_FIELDS.items():
            try:
                record[out] = float(row[field])
            except (KeyError, TypeError, ValueError):
                record[out] = None
        records.append(record)

    if not records:
        return pl.DataFrame()
    return pl.DataFrame(records, infer_schema_length=None).with_columns(
        pl.lit(SOURCE).alias("source"),
    )


def _has_rows() -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            f"SELECT count(*) FROM {TABLE} WHERE source = ?", [SOURCE]
        ).fetchone()
    except duckdb.CatalogException:
        return False
    finally:
        conn.close()
    return bool(row and row[0])


def collect_export_sales(
    *,
    since: date | None = None,
    tracker: SourceTracker | None = None,
    bulk_backfill: bool | None = None,
) -> int:
    """Collect USDA weekly export sales into us_export_sales.

    With no explicit ``since``, the full history is fetched only when bulk
    backfill is allowed (CI) and the table is empty; otherwise the last
    ``RECENT_WEEKS`` weeks are re-fetched.
    """
    if tracker is None:
        tracker = SourceTracker()
    if bulk_backfill is None:
        bulk_backfill = settings.allow_bulk_backfill
    if since is None and not (bulk_backfill and not _has_rows()):
        since = date.today() - timedelta(weeks=RECENT_WEEKS)

    with TimedCollector(tracker, SOURCE) as tc:
        df = parse_export_sales(fetch_export_sales(since))
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No USDA export sales rows since %s", since)
            return 0
        count = write_raw(SOURCE, df, table_name=TABLE)
        tc.rows_written = count
        return count
