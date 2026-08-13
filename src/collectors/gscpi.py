"""Collect the NY Fed Global Supply Chain Pressure Index (GSCPI).

The index is published monthly by the Federal Reserve Bank of New York
and is keyless. The interactive data file is a wide-format CSV where each
column is a revision vintage; the last non-empty, non-"#N/A" column is the
latest estimate for that month. Coverage runs from 1997 to the present.
"""
from __future__ import annotations

import csv
import io
import logging
import re
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

GSCPI_CSV_URL = (
    "https://www.newyorkfed.org/medialibrary/research/interactives/data/"
    "gscpi/gscpi_interactive_data.csv"
)
SOURCE = "nyfed_gscpi"

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_nyfed_date(text: str) -> date | None:
    """Parse NY Fed date strings like ``30-Sep-1997`` into a date."""
    match = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{4})", text.strip())
    if not match:
        return None
    day_text, month_name, year_text = match.groups()
    month = _MONTHS.get(month_name.lower())
    if month is None:
        return None
    try:
        return date(int(year_text), month, int(day_text))
    except ValueError:
        return None


def fetch_gscpi_data() -> str | None:
    """Fetch the GSCPI wide-format CSV as text."""
    logger.info("Fetching GSCPI data from NY Fed")
    resp = requests.get(GSCPI_CSV_URL, headers={"User-Agent": "shipping-data-pipeline"}, timeout=30)
    if resp.status_code != 200:
        logger.warning("GSCPI CSV returned status %d", resp.status_code)
        return None
    return resp.text


def parse_gscpi_csv(content: str | None) -> pl.DataFrame:
    """Parse the wide-format CSV, keeping the latest vintage per month."""
    if not content:
        return pl.DataFrame()

    rows: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(content))
    header = next(reader, None)
    if header is None:
        return pl.DataFrame()

    for line in reader:
        if not line or not line[0].strip():
            continue
        obs_date = _parse_nyfed_date(line[0])
        if obs_date is None:
            continue
        value: float | None = None
        for cell in reversed(line[1:]):
            cell = cell.strip()
            if cell and cell != "#N/A":
                try:
                    value = float(cell)
                except ValueError:
                    continue
                break
        if value is None:
            continue
        rows.append({
            "index_date": obs_date,
            "gscpi_index": value,
        })

    if not rows:
        return pl.DataFrame()

    return pl.DataFrame(rows).with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )


def collect_gscpi_data(tracker: SourceTracker | None = None) -> int:
    """Collect GSCPI data and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        content = fetch_gscpi_data()
        df = parse_gscpi_csv(content)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No GSCPI records parsed")
            return 0

        logger.info("Writing %d GSCPI records", df.height)
        count = write_raw(SOURCE, df, table_name="supply_chain_index")
        tc.rows_written = count
        return count
