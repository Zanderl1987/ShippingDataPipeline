"""Collect Eurostat maritime freight statistics (dataset ``mar_go_aa``).

Eurostat's JSON-stat dissemination API is keyless. ``mar_go_aa`` is the
annual maritime freight throughput dataset: thousand tonnes moved through
each reporting EU maritime country/port, split into inwards/outwards/total
flows. Coverage runs from 1997 to the present.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

# Eurostat JSON-stat dissemination API
EUROSTAT_BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data"
DATASET_ID = "mar_go_aa"
SOURCE = "eurostat_maritime"

# Dimension names in the dataset, mapped to the flat record keys
DIM_TO_FIELD = {
    "time": "time_period",
    "rep_mar": "geo",
    "direct": "direction",
    "unit": "unit",
}


def fetch_eurostat_maritime_data() -> dict[str, Any]:
    """Fetch the Eurostat maritime freight dataset as JSON-stat."""
    url = f"{EUROSTAT_BASE_URL}/{DATASET_ID}"
    logger.info("Fetching Eurostat maritime dataset %s", DATASET_ID)
    resp = requests.get(url, params={"format": "JSON", "lang": "en"}, timeout=60)
    if resp.status_code != 200:
        logger.warning("Eurostat Maritime API returned HTTP %d", resp.status_code)
        return {}
    data = resp.json()
    return data if isinstance(data, dict) else {}


def flatten_jsonstat(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten a JSON-stat value array into one record per observation.

    The ``dimension`` object lists dimensions in the same order as the value
    array's mixed-radix index (last dimension varies fastest). Labels are
    carried alongside codes so downstream consumers don't need a second
    lookup table.
    """
    dims = payload.get("dimension", {})
    if not dims:
        return []

    dim_meta: list[tuple[str, list[Any], dict[str, str]]] = []
    for name, dim in dims.items():
        cat = dim.get("category", {})
        index = cat.get("index", {})
        labels: dict[str, str] = cat.get("label", {})
        if isinstance(index, dict):
            ordered: list[Any] = [None] * len(index)
            for code_key, pos in index.items():
                if isinstance(pos, int) and 0 <= pos < len(ordered):
                    ordered[pos] = code_key
        else:
            ordered = list(index)
        dim_meta.append((name, ordered, labels))

    sizes = [len(meta[1]) for meta in dim_meta]
    if any(s == 0 for s in sizes):
        return []

    values = payload.get("value", {})
    records: list[dict[str, Any]] = []
    items = values.items() if isinstance(values, dict) else enumerate(values)
    for raw_index, value in items:
        if value is None:
            continue
        combo: list[int] = []
        pos = int(raw_index)
        for size in reversed(sizes):
            combo.append(pos % size)
            pos //= size
        combo.reverse()

        record: dict[str, Any] = {"value": float(value)}
        for (name, codes, labels), i in zip(dim_meta, combo):
            raw_code = codes[i] if i < len(codes) else None
            code: str | None = (
                raw_code if isinstance(raw_code, str)
                else str(raw_code) if raw_code is not None else None
            )
            field = DIM_TO_FIELD.get(name, name)
            record[field] = code
            record[f"{field}_label"] = labels.get(code, code) if code is not None else None
        records.append(record)
    return records


def parse_eurostat_maritime_records(payload: dict[str, Any]) -> pl.DataFrame:
    """Parse a JSON-stat payload into a structured Polars DataFrame."""
    records = flatten_jsonstat(payload)
    if not records:
        return pl.DataFrame()

    cols = [
        "time_period", "geo", "geo_label", "direction",
        "direction_label", "unit", "unit_label", "value",
    ]
    df = pl.DataFrame([{k: r.get(k) for k in cols} for r in records]).with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )
    return df


def collect_eurostat_maritime_data(tracker: SourceTracker | None = None) -> int:
    """Collect Eurostat maritime freight throughput and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        payload = fetch_eurostat_maritime_data()
        df = parse_eurostat_maritime_records(payload)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Eurostat Maritime data returned")
            return 0

        logger.info("Writing %d Eurostat Maritime records", df.height)
        count = write_raw(SOURCE, df, table_name="maritime_freight")
        tc.rows_written = count
        return count
