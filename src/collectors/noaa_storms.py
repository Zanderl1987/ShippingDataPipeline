"""Collect NOAA Storm Events Database records.

Severe weather with damage estimates is a disruption signal for shipping,
agriculture, insurance and energy. The bulk per-year details CSVs on the
NCEI SWDI server are keyless and regenerated whenever the database is
updated. File names embed a per-year compile date, so the directory listing
is parsed rather than assuming a stable name.
"""
from __future__ import annotations

import csv
import gzip
import io
import logging
import re
from datetime import date, datetime
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

CSVFILES_URL = "https://www.ncei.noaa.gov/pub/data/swdi/stormevents/csvfiles/"
SOURCE = "noaa_storms"
# How many most-recent years each run fetches. A one-off full backfill can be
# done by temporarily raising this (the files cover 1950 onward).
DEFAULT_YEARS_BACK = 3

_CHROME = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}


def list_storm_files() -> dict[int, str]:
    """Map available year -> details CSV URL from the SWDI listing."""
    resp = requests.get(CSVFILES_URL, headers=_CHROME, timeout=40)
    if resp.status_code != 200:
        logger.warning("NCEI SWDI listing returned status %d", resp.status_code)
        return {}
    files: dict[int, str] = {}
    for match in re.finditer(r'href="([^"]*StormEvents_details[^"]+\.gz)"', resp.text):
        url, name = match.group(1), match.group(1)
        year_match = re.search(r"_d(\d{4})_", name)
        if year_match:
            files[int(year_match.group(1))] = url if url.startswith("http") else CSVFILES_URL + url
    return files


def fetch_storm_csv(url: str) -> str | None:
    """Download and decompress one details CSV."""
    logger.info("Fetching NOAA storm events %s", url.rsplit("/", 1)[-1])
    resp = requests.get(url, headers=_CHROME, timeout=120)
    if resp.status_code != 200:
        logger.warning("NCEI storm CSV returned status %d", resp.status_code)
        return None
    # The .csv.gz payload is stored gzip, not HTTP-encoded, so requests leaves
    # it compressed; decompress before decoding.
    return gzip.decompress(resp.content).decode("utf-8", errors="replace")


def parse_storm_csv(content: str | None) -> pl.DataFrame:
    """Parse a details CSV into the storm_events shape."""
    if not content:
        return pl.DataFrame()

    # The details files contain embedded newlines inside some narrative
    # fields; newline="" lets the csv module treat them as data.
    reader = csv.DictReader(io.StringIO(content, newline=""))
    if not reader.fieldnames:
        return pl.DataFrame()

    rows: list[dict[str, Any]] = []
    for rec in reader:
        rows.append({
            "event_id": _clean(rec.get("EVENT_ID")),
            "event_type": _clean(rec.get("EVENT_TYPE")),
            "begin_date": _parse_ts(rec.get("BEGIN_DATE_TIME")),
            "end_date": _parse_ts(rec.get("END_DATE_TIME")),
            "state": _clean(rec.get("STATE")),
            "county": _clean(rec.get("CZ_NAME")),
            "latitude": _to_float(rec.get("BEGIN_LAT")),
            "longitude": _to_float(rec.get("BEGIN_LON")),
            "injuries_direct": _to_int(rec.get("INJURIES_DIRECT")),
            "injuries_indirect": _to_int(rec.get("INJURIES_INDIRECT")),
            "deaths_direct": _to_int(rec.get("DEATHS_DIRECT")),
            "deaths_indirect": _to_int(rec.get("DEATHS_INDIRECT")),
            "damage_property_millions": _damage_to_millions(rec.get("DAMAGE_PROPERTY")),
            "damage_crops_millions": _damage_to_millions(rec.get("DAMAGE_CROPS")),
        })

    if not rows:
        return pl.DataFrame()

    return pl.DataFrame(rows).with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _to_float(value: Any) -> float | None:
    if value is None or str(value).strip() in ("", "nan"):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def _to_int(value: Any) -> int | None:
    f = _to_float(value)
    return int(f) if f is not None else None


def _parse_ts(value: Any) -> datetime | None:
    text = _clean(value)
    if not text:
        return None
    # Details CSV timestamps look like "14-APR-26 19:10:00" (DD-MMM-YY).
    match = re.fullmatch(r"(\d{1,2})-([A-Za-z]{3})-(\d{2}) (\d{2}):(\d{2}):(\d{2})", text)
    if not match:
        return None
    day, month_name, year_2, hh, mm, ss = match.groups()
    months = {
        "JAN": 1, "FEB": 2, "MAR": 3, "APR": 4, "MAY": 5, "JUN": 6,
        "JUL": 7, "AUG": 8, "SEP": 9, "OCT": 10, "NOV": 11, "DEC": 12,
    }
    month = months.get(month_name.upper())
    if month is None:
        return None
    year = 2000 + int(year_2) if int(year_2) < 70 else 1900 + int(year_2)
    try:
        return datetime(year, month, int(day), int(hh), int(mm), int(ss))
    except ValueError:
        return None


def _damage_to_millions(value: Any) -> float | None:
    """Convert NWS damage strings (``10.00K``, ``5.00M``, ``1.0B``, plain) to USD millions."""
    text = _clean(value)
    if not text:
        return None
    match = re.fullmatch(r"([\d.,]+)([KMB])?", text, re.IGNORECASE)
    if not match:
        return None
    number = float(match.group(1).replace(",", ""))
    unit = (match.group(2) or "").upper()
    factor = {"": 1e-6, "K": 1e-3, "M": 1.0, "B": 1e3}.get(unit)
    if factor is None:
        return None
    return number * factor


def collect_noaa_storms_data(
    tracker: SourceTracker | None = None, years_back: int = DEFAULT_YEARS_BACK
) -> int:
    """Collect recent NOAA storm event records and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        files = list_storm_files()
        recent = [y for y in sorted(files) if y >= date.today().year - years_back + 1]
        if not recent:
            logger.warning("No NOAA storm files found")
            return 0

        frames = []
        for year in sorted(recent):
            content = fetch_storm_csv(files[year])
            frame = parse_storm_csv(content)
            if frame.height:
                frames.append(frame)
        if not frames:
            return 0

        df = pl.concat(frames)
        tc.rows_fetched = df.height
        logger.info("Writing %d NOAA storm event records", df.height)
        count = write_raw(SOURCE, df, table_name="storm_events")
        tc.rows_written = count
        return count
