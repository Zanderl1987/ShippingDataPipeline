"""Collect BTS T-100 air cargo statistics (freight poundage by route).

TranStats serves T-100 Market data through an ASP.NET WebForms flow: a GET
to the field-selection page yields __VIEWSTATE tokens, and a form POST with
the chosen fields, year and period returns a ZIP containing a CSV. The
column set is deliberately restricted to keep the downloads small; a full
all-fields year is 10x larger and carries nothing we need.

Two market databases are pulled: T-100 Domestic (``FIL``) and T-100
International US-carrier (``GDJ``) Market. ``MONTH`` values are 1-12; the
requested year is injected because it is not part of the selected columns.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

FORM_URL = "https://www.transtats.bts.gov/DL_SelectFields.aspx"
SOURCE = "bts_t100"
# tableid -> label for the market databases pulled
DATABASES: dict[str, str] = {
    "FIL": "T-100 Domestic Market",
    "GDJ": "T-100 International Market (US Carriers Only)",
}
DB_SHORT_NAME = "Nv4 Pn44vr45"  # ROT13 obfuscated "Air Carriers"

# Fields checked on the selection form; the response CSV is in this order.
FIELDS = [
    "YEAR", "MONTH", "FREIGHT", "PASSENGERS", "UNIQUE_CARRIER",
    "UNIQUE_CARRIER_NAME", "ORIGIN", "DEST",
]

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _fetch_form(session: requests.Session, table_id: str) -> str | None:
    """GET the field-selection page and return its HTML."""
    url = f"{FORM_URL}?gnoyr_VQ={table_id}&QO_fu146_anzr={DB_SHORT_NAME.replace(' ', '%20')}"
    resp = session.get(url, headers={"User-Agent": _UA}, timeout=60)
    if resp.status_code != 200:
        logger.warning("BTS form for %s returned status %d", table_id, resp.status_code)
        return None
    return resp.text


def _hidden_value(html: str, name: str) -> str:
    match = re.search(rf'id="{name}" value="([^"]*)"', html)
    return match.group(1) if match else ""


def download_year(session: requests.Session, table_id: str, year: int) -> str | None:
    """Download one year of T-100 Market data and return the CSV text."""
    html = _fetch_form(session, table_id)
    if html is None:
        return None

    body: dict[str, str] = {
        "__EVENTARGUMENT": "",
        "__LASTFOCUS": "",
        "__VIEWSTATE": _hidden_value(html, "__VIEWSTATE"),
        "__VIEWSTATEGENERATOR": _hidden_value(html, "__VIEWSTATEGENERATOR"),
        "__EVENTVALIDATION": _hidden_value(html, "__EVENTVALIDATION"),
        "txtSearch": "",
        "btnDownload": "Download",
        "cboGeography": "All",
        "cboYear": str(year),
        "cboPeriod": "All",
    }
    for field in FIELDS:
        body[field] = "on"

    url = f"{FORM_URL}?gnoyr_VQ={table_id}&QO_fu146_anzr={DB_SHORT_NAME.replace(' ', '+')}"
    resp = session.post(url, headers={"User-Agent": _UA}, data=body, timeout=180)
    if resp.status_code != 200 or resp.content[:2] != b"PK":
        logger.warning(
            "BTS download for %d (%s) failed: status=%d", year, table_id, resp.status_code
        )
        return None

    with zipfile.ZipFile(io.BytesIO(resp.content)) as archive:
        csv_name = archive.namelist()[0]
        return archive.read(csv_name).decode("latin-1")


def parse_t100_csv(content: str | None, year: int) -> pl.DataFrame:
    """Parse a T-100 Market CSV into the air_cargo shape."""
    if not content:
        return pl.DataFrame()

    try:
        df = pl.read_csv(io.StringIO(content), infer_schema_length=0)
    except Exception as exc:
        logger.warning("Failed to parse T-100 CSV for %d: %s", year, exc)
        return pl.DataFrame()

    expected = {
        "MONTH", "FREIGHT", "PASSENGERS", "UNIQUE_CARRIER",
        "UNIQUE_CARRIER_NAME", "ORIGIN", "DEST",
    }
    missing = expected - set(df.columns)
    for col in missing:
        df = df.with_columns(pl.lit(None, dtype=pl.String).alias(col))

    rows = [
        {
            "cargo_year": year,
            "cargo_month": _to_int(df[i, "MONTH"]),
            "carrier_code": _clean(df[i, "UNIQUE_CARRIER"]),
            "carrier_name": _clean(df[i, "UNIQUE_CARRIER_NAME"]),
            "origin": _clean(df[i, "ORIGIN"]),
            "dest": _clean(df[i, "DEST"]),
            "freight_pounds": _to_float(df[i, "FREIGHT"]),
            "passengers": _to_float(df[i, "PASSENGERS"]),
        }
        for i in range(df.height)
    ]
    rows = [r for r in rows if r["cargo_month"] is not None]

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


def _to_int(value: Any) -> int | None:
    if value is None or str(value).strip() in ("", "nan"):
        return None
    try:
        return int(float(str(value).strip()))
    except ValueError:
        return None


def _to_float(value: Any) -> float | None:
    if value is None or str(value).strip() in ("", "nan"):
        return None
    try:
        return float(str(value).strip())
    except ValueError:
        return None


def collect_bts_air_cargo_data(
    tracker: SourceTracker | None = None, years: list[int] | None = None
) -> int:
    """Collect T-100 air cargo for the given years (default: current year)."""
    if tracker is None:
        tracker = SourceTracker()
    if years is None:
        years = [date.today().year]

    with TimedCollector(tracker, SOURCE) as tc:
        session = requests.Session()
        frames = []
        for table_id in DATABASES:
            for year in years:
                content = download_year(session, table_id, year)
                frame = parse_t100_csv(content, year)
                if frame.height:
                    frames.append(frame)
                    logger.info("%s %d: %d rows", DATABASES[table_id], year, frame.height)
                else:
                    logger.warning("%s %d: no rows parsed", DATABASES[table_id], year)
        if not frames:
            return 0

        df = pl.concat(frames)
        tc.rows_fetched = df.height
        logger.info("Writing %d BTS air cargo records", df.height)
        count = write_raw(SOURCE, df, table_name="air_cargo")
        tc.rows_written = count
        return count
