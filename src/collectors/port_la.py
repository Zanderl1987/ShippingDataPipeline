"""Collect Port of Los Angeles monthly container throughput (TEU) statistics.

Two keyless feeds are combined:
1. Historical monthly TEU (2009-2016) via the official Socrata dataset
   ``tsuv-4rgh`` on data.lacity.org ("Port of Los Angeles - TEU Counts
   Monthly And Calendar YTD").
2. Per-year "Historical TEU Statistics" pages on portoflosangeles.org
   (2017 to the current year), which carry the full import/export
   breakdown as an HTML table.

Socrata is the official open-data mirror; the per-year pages are the
current signal (the Socrata table stops in 2016).
"""
from __future__ import annotations

import html
import logging
import re
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

# Port of Los Angeles monthly TEU dataset on the LA city open-data portal
SOCRATA_ENDPOINT = "https://data.lacity.org/resource/tsuv-4rgh.json"
POLA_SITE_TPL = (
    "https://www.portoflosangeles.org/business/statistics/"
    "container-statistics/Historical-TEU-Statistics-{year}"
)
# Socrata coverage ends in 2016; per-year pages pick up from 2017.
FIRST_PAGE_YEAR = 2017

SOURCE = "port_la"
_PORT_CODE = "USLAX"

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5,
    "june": 6, "july": 7, "august": 8, "september": 9, "october": 10,
    "november": 11, "december": 12,
}

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _to_float(value: Any) -> float | None:
    """Parse a number cell that may carry thousands separators."""
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if text in ("", "-", "n/a", "N/A"):
        return None
    try:
        return float(text)
    except ValueError:
        return None


def fetch_socrata_historical(limit: int = 5000) -> list[dict[str, Any]]:
    """Fetch the Socrata monthly total-TEU records (2009-2016)."""
    logger.info("Fetching Port of LA historical TEU from Socrata")
    resp = requests.get(
        SOCRATA_ENDPOINT,
        params={"$limit": str(limit), "$order": "date ASC"},
        timeout=30,
    )
    if resp.status_code != 200:
        logger.warning("Port of LA Socrata returned status %d", resp.status_code)
        return []
    data = resp.json()
    return data if isinstance(data, list) else []


def parse_socrata_historical(raw_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Normalize Socrata rows into the shared port_volumes shape."""
    records = []
    for rec in raw_records:
        month_year = rec.get("month_year", "")
        date_match = re.fullmatch(r"(\w{3})-(\d{2})", str(month_year).strip())
        if not date_match:
            continue
        month_abbrev, year_str = date_match.groups()
        month = {
            "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
            "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
        }.get(month_abbrev.lower())
        if month is None:
            continue
        period_date = date(int(year_str) + 2000 if len(year_str) == 2 else int(year_str), month, 1)
        records.append({
            "period": f"{period_date:%Y-%m}",
            "period_date": period_date,
            "total_teu": _to_float(rec.get("monthly_total_teus")),
            "prior_year_change_pct": None,
        })
    return records


def fetch_year_page(year: int) -> list[dict[str, Any]]:
    """Scrape one 'Historical TEU Statistics' page into port_volumes rows."""
    url = POLA_SITE_TPL.format(year=year)
    logger.info("Fetching Port of LA TEU page for %d", year)
    resp = requests.get(url, headers={"User-Agent": _UA}, timeout=30)
    if resp.status_code != 200:
        logger.warning("Port of LA page for %d returned status %d", year, resp.status_code)
        return []
    return parse_year_page_html(resp.text, year)


def parse_year_page_html(html_text: str, year: int) -> list[dict[str, Any]]:
    """Extract monthly rows from a raw 'Historical TEU Statistics' page."""
    tables = re.findall(r"<table[^>]*>.*?</table>", html_text, re.S | re.I)
    if not tables:
        return []

    records = []
    for table in tables:
        rows = re.findall(r"<tr[^>]*>(.*?)</tr>", table, re.S | re.I)
        for row in rows:
            cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.S | re.I)
            cells = [re.sub(r"<[^>]+>", "", c).replace("\xa0", " ").strip() for c in cells]
            cells = [html.unescape(c) for c in cells]
            if len(cells) < 9:
                continue
            month_name = cells[0].lower()
            if month_name not in _MONTHS:
                continue
            total = _to_float(cells[7])
            if total is None:
                total_imports = _to_float(cells[3])
                total_exports = _to_float(cells[6])
                if total_imports is not None and total_exports is not None:
                    total = total_imports + total_exports
                else:
                    continue
            period_date = date(year, _MONTHS[month_name], 1)
            records.append({
                "period": f"{period_date:%Y-%m}",
                "period_date": period_date,
                "loaded_imports_teu": _to_float(cells[1]),
                "empty_imports_teu": _to_float(cells[2]),
                "total_imports_teu": _to_float(cells[3]),
                "loaded_exports_teu": _to_float(cells[4]),
                "empty_exports_teu": _to_float(cells[5]),
                "total_exports_teu": _to_float(cells[6]),
                "total_teu": total,
                "prior_year_change_pct": _to_float(
                    cells[8].replace("%", "").replace("+", "")
                ),
            })
    return records


def parse_port_la_records(
    historical: list[dict[str, Any]], pages: list[dict[str, Any]]
) -> pl.DataFrame:
    """Combine the two feeds into a single structured DataFrame."""
    records = [_base_row(rec) for rec in parse_socrata_historical(historical)]
    records.extend(_base_row(rec) for rec in pages)

    if not records:
        return pl.DataFrame()

    return pl.DataFrame(records).with_columns(
        pl.lit(date.today()).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )


def _base_row(rec: dict[str, Any]) -> dict[str, Any]:
    row: dict[str, Any] = {
        "port_code": _PORT_CODE,
        "period": rec.get("period"),
        "period_date": rec.get("period_date"),
        "loaded_imports_teu": rec.get("loaded_imports_teu"),
        "empty_imports_teu": rec.get("empty_imports_teu"),
        "total_imports_teu": rec.get("total_imports_teu"),
        "loaded_exports_teu": rec.get("loaded_exports_teu"),
        "empty_exports_teu": rec.get("empty_exports_teu"),
        "total_exports_teu": rec.get("total_exports_teu"),
        "total_teu": rec.get("total_teu"),
        "prior_year_change_pct": rec.get("prior_year_change_pct"),
    }
    return {k: v for k, v in row.items()}


def collect_port_la_data(tracker: SourceTracker | None = None) -> int:
    """Collect Port of LA container throughput and write to storage."""
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        historical = fetch_socrata_historical()
        pages = []
        current_year = date.today().year
        for year in range(FIRST_PAGE_YEAR, current_year + 1):
            pages.extend(fetch_year_page(year))

        df = parse_port_la_records(historical, pages)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Port of LA data returned")
            return 0

        logger.info("Writing %d Port of LA records", df.height)
        count = write_raw(SOURCE, df, table_name="port_volumes")
        tc.rows_written = count
        return count
