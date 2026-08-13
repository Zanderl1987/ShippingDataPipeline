"""Collect Singapore Maritime and Port Authority (MPA) statistics.

Singapore is the world's busiest transshipment hub, so its vessel arrival,
call and fleet-registry figures are a pulse for Asian seaborne trade. The
data.gov.sg datastore API is keyless but rate-limited: 4 searches per 10s
without an API key (2 downloads per 10s). The three datasets used here are
small enough that a single paginated pass each keeps us well under that.
"""
from __future__ import annotations

import logging
import time
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

DATASTORE_URL = "https://data.gov.sg/api/action/datastore_search"
SOURCE = "singapore_mpa"
# Keyless datastore_search is limited to 4 calls per 10 seconds; space calls
# out and back off on 429s instead of dropping rows.
_RATE_LIMIT_SLEEP = 2.5
_MAX_RETRIES = 4

# resource_id -> (metric_name, annual_or_monthly, value_field, category_field)
DATASETS: dict[str, tuple[str, str, str, str | None]] = {
    "d_60410de1bc1e63ddcf51a619081b11b3": (
        "vessel_calls", "annual", "number_of_vessel_calls", "purpose_type"
    ),
    "d_8392e9bea6ca351a38f67172ccdf6a6a": ("vessel_arrivals", "annual", "number_of_vessels", None),
    "d_56f64b2d5a31eb0ee465cc51e83ac60a": (
        "registered_vessels", "monthly", "number_of_vessels", None
    ),
}

PAGE_SIZE = 100


def fetch_dataset(resource_id: str) -> list[dict[str, Any]]:
    """Fetch all rows of one datastore dataset via paginated searches."""
    records: list[dict[str, Any]] = []
    offset = 0
    while True:
        resp = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = requests.get(
                    DATASTORE_URL,
                    params={
                        "resource_id": resource_id,
                        "limit": str(PAGE_SIZE),
                        "offset": str(offset),
                    },
                    timeout=30,
                )
            except requests.RequestException as exc:
                logger.warning("data.gov.sg request error: %s", exc)
                time.sleep(_RATE_LIMIT_SLEEP)
                continue
            if resp.status_code == 429 and attempt < _MAX_RETRIES - 1:
                logger.warning("data.gov.sg rate limit hit; backing off")
                time.sleep(_RATE_LIMIT_SLEEP * (attempt + 1))
                continue
            break
        if resp is None or resp.status_code != 200:
            logger.warning(
                "data.gov.sg search for %s failed (status %s)",
                resource_id,
                resp.status_code if resp else "no response",
            )
            break
        payload = resp.json()
        result = payload.get("result") or {}
        batch = result.get("records", [])
        records.extend(batch)
        total = result.get("total") or 0
        offset += len(batch)
        if offset >= total or not batch:
            break
        time.sleep(_RATE_LIMIT_SLEEP)
    return records


def parse_singapore_metrics(all_records: dict[str, list[dict[str, Any]]]) -> pl.DataFrame:
    """Normalize the three MPA datasets into the shared port_metrics shape."""
    rows: list[dict[str, Any]] = []
    for resource_id, (metric, freq, value_field, category_field) in DATASETS.items():
        for rec in all_records.get(resource_id, []):
            period = str(rec.get("month", rec.get("year", ""))).strip()
            year = int(period[:4]) if period[:4].isdigit() else None
            value = _to_float(rec.get(value_field))
            if year is None or value is None:
                continue
            category = (
                str(rec.get(category_field, "ALL") or "ALL").strip()
                if category_field
                else "ALL"
            )
            if not category:
                category = "ALL"
            rows.append({
                "metric_period": period,
                "metric_year": year,
                "metric_name": metric,
                "category": category,
                "value": value,
            })

    if not rows:
        return pl.DataFrame()

    return pl.DataFrame(rows).with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )


def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def collect_singapore_mpa_data(tracker: SourceTracker | None = None) -> int:
    """Collect Singapore MPA port metrics and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        fetched: dict[str, list[dict[str, Any]]] = {}
        for resource_id in DATASETS:
            fetched[resource_id] = fetch_dataset(resource_id)
        df = parse_singapore_metrics(fetched)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Singapore MPA records parsed")
            return 0

        logger.info("Writing %d Singapore MPA records", df.height)
        count = write_raw(SOURCE, df, table_name="port_metrics")
        tc.rows_written = count
        return count
