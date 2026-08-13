"""Collect the OFAC Specially Designated Nationals (SDN) list.

The SDN list is the US Treasury's master list of sanctioned individuals,
entities, aircraft and vessels. The shipping relevance is direct: sanctioned
vessels and the shadow tanker fleet are identified here before they surface
in AIS data. The CSV export at treasury.gov is keyless, updated daily, and
roughly 18k records.
"""
from __future__ import annotations

import csv
import io
import logging
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

SDN_CSV_URL = "https://www.treasury.gov/ofac/downloads/sdn.csv"
SOURCE = "ofac_sdn"

# Positional layout of the sdn.csv export. `-0-` marks a missing value.
_FIELDS = [
    "ent_num", "sdn_name", "sdn_type", "program", "title", "call_sign",
    "vessel_type", "tonnage", "grt", "vessel_flag", "vessel_owner", "remarks",
]


def fetch_sdn_data() -> str | None:
    """Fetch the SDN CSV as text."""
    logger.info("Fetching OFAC SDN list")
    resp = requests.get(SDN_CSV_URL, headers={"User-Agent": "shipping-data-pipeline"}, timeout=60)
    if resp.status_code != 200:
        logger.warning("OFAC SDN CSV returned status %d", resp.status_code)
        return None
    return resp.text


def _clean(value: str | None) -> str | None:
    """Treat OFAC's ``-0-`` placeholder as missing."""
    if value is None:
        return None
    value = value.strip()
    if value in ("", "-0-"):
        return None
    return value


def parse_sdn_csv(content: str | None) -> pl.DataFrame:
    """Parse the SDN CSV into a structured DataFrame."""
    if not content:
        return pl.DataFrame()

    rows: list[dict[str, Any]] = []
    reader = csv.reader(io.StringIO(content))
    for i, line in enumerate(reader):
        if not line or not line[0].strip():
            continue
        if i == 0 and line[0].strip().lower() == "ent_num":
            continue
        fields = list(line) + [None] * (len(_FIELDS) - len(line))
        rec = dict(zip(_FIELDS, fields))
        if not rec["ent_num"]:
            continue
        rows.append({
            "entity_id": rec["ent_num"],
            "name": _clean(rec["sdn_name"]),
            "entity_type": _clean(rec["sdn_type"]),
            "programs": _clean(rec["program"]),
            "title": _clean(rec["title"]),
            "call_sign": _clean(rec["call_sign"]),
            "vessel_type": _clean(rec["vessel_type"]),
            "vessel_tonnage": _clean(rec["tonnage"]),
            "gross_registered_tonnage": _clean(rec["grt"]),
            "vessel_flag": _clean(rec["vessel_flag"]),
            "country": _clean(rec["vessel_owner"]),
            "remarks": _clean(rec["remarks"]),
            "aliases": None,
        })

    if not rows:
        return pl.DataFrame()

    return pl.DataFrame(rows).with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )


def collect_ofac_sanctions_data(tracker: SourceTracker | None = None) -> int:
    """Collect the OFAC SDN list and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        content = fetch_sdn_data()
        df = parse_sdn_csv(content)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No OFAC SDN records parsed")
            return 0

        logger.info("Writing %d OFAC SDN records", df.height)
        count = write_raw(SOURCE, df, table_name="sanctions")
        tc.rows_written = count
        return count
