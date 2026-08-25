"""Collect the canonical UN/LOCODE port/location reference from UNECE.

UN/LOCODE (https://unece.org/trade/uncefact/unlocode) is the authoritative
global code list for ports and other trade/transport locations — 116K+
locations, released biannually. Open data (ODC-PDDL), no auth.

Download: the GitLab package-release job artifact zip (~13.5 MB) containing
the 3 CSV code-list parts. The CSVs are COMMA-delimited with 12 positional
columns and no header row (verified against the 2025-1 release):

    change, country, location, name, name_wo_diacritics, subdiv,
    function, status, date, iata, coordinates, remarks

Coordinates use the UNECE DDMM format ('4230N 00131E') and are converted to
decimal degrees. All rows are loaded keyed by unlocode; the function
classifier is preserved per row so consumers can filter to ports
(function class '1' = Port or one or more ports).

The `ports` table has a unlocode PRIMARY KEY, so write_raw's INSERT OR
REPLACE path overwrites the Digitraffic-derived subset on re-runs.
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

ARTIFACT_URL = (
    "https://opensource.unicc.org/un/unece/uncefact/vocab-locode/"
    "-/jobs/artifacts/2025-1/download?job=package-release"
)

SOURCE = "unlocode"

# Positional columns of the code-list CSV parts (no header row).
_COLUMNS = [
    "change",
    "country",
    "location",
    "name",
    "name_wo_diacritics",
    "subdiv",
    "function",
    "status",
    "entry_date",
    "iata",
    "coordinates",
    "remarks",
]


def download_zip() -> bytes:
    """Download the UN/LOCODE release zip (~13.5 MB)."""
    resp = get_with_retry(ARTIFACT_URL, timeout=300, source=SOURCE)
    return resp.content


def _parse_coordinate(token: str | None) -> float | None:
    """Convert a UNECE coordinate token to decimal degrees.

    Latitude format is DDMM[N/S], longitude DDDMM[E/W] — e.g.
    '4230N' -> 42.5, '00131E' -> 1.516667. Returns None for missing,
    empty, or '--' placeholders and for malformed values.
    """
    if not isinstance(token, str):
        return None
    token = token.strip()
    if len(token) < 5 or token.startswith("-"):
        return None
    hemisphere = token[-1].upper()
    if hemisphere in ("N", "S"):
        digits = token[:-1]
    elif hemisphere in ("E", "W"):
        digits = token[:-1]
    else:
        return None
    try:
        degrees = int(digits[: len(digits) - 2])
        minutes = int(digits[-2:])
    except ValueError:
        return None
    value = degrees + minutes / 60.0
    return -value if hemisphere in ("S", "W") else value


def _parse_coordinates(pair: Any) -> tuple[float | None, float | None]:
    """Split a coordinates field like '4230N 00131E' into (lat, lon)."""
    if not isinstance(pair, str):
        return None, None
    parts = pair.strip().split()
    lat = _parse_coordinate(parts[0]) if parts else None
    lon = _parse_coordinate(parts[1]) if len(parts) > 1 else None
    return lat, lon


def parse_code_list_rows(text: str) -> list[dict[str, str]]:
    """Parse one comma-delimited code-list part into raw row dicts."""
    reader = csv.reader(io.StringIO(text))
    rows: list[dict[str, str]] = []
    for fields in reader:
        if not fields or not any(f.strip() for f in fields):
            continue
        record = dict(zip(_COLUMNS, fields))
        # Country header lines carry only the country code + name and no
        # location component — skip them.
        if not record.get("location", "").strip():
            continue
        rows.append(record)
    return rows


def extract_code_lists(zip_bytes: bytes) -> list[str]:
    """Extract the text of every code-list CSV part from the release zip."""
    texts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        for member in zf.namelist():
            base = member.rsplit("/", 1)[-1]
            if base.startswith("UNLOCODE CodeList") and base.lower().endswith(".csv"):
                texts.append(zf.read(member).decode("utf-8"))
    return texts


def _build_records(rows: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Convert parsed CSV rows into ports-table records.

    Every row is kept; function_class preserves the classifier string so
    port rows (class '1') can be filtered in SQL.
    """
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for r in rows:
        country = (r.get("country") or "").strip()
        location = (r.get("location") or "").strip()
        locode = f"{country}{location}"
        if not locode.strip() or locode in seen:
            continue
        seen.add(locode)
        lat, lon = _parse_coordinates(r.get("coordinates"))
        name = (r.get("name") or "").strip()
        if not name:
            continue
        records.append({
            "unlocode": locode.upper(),
            "port_name": name,
            "country_code": country.upper(),
            "latitude": lat,
            "longitude": lon,
            "function_class": (r.get("function") or "").strip() or None,
            "status": (r.get("status") or "").strip() or None,
        })
    return records


def parse_code_lists(zip_bytes: bytes) -> pl.DataFrame:
    """Parse all code-list parts from the release zip into the ports schema."""
    texts = extract_code_lists(zip_bytes)
    if not texts:
        logger.warning("No UNLOCODE CodeList CSV parts found in the release zip")
        return pl.DataFrame()

    rows: list[dict[str, str]] = []
    for text in texts:
        rows.extend(parse_code_list_rows(text))
    logger.info("Parsed %d UN/LOCODE rows from %d CSV parts", len(rows), len(texts))

    records = _build_records(rows)
    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    df = df.with_columns(pl.lit(SOURCE).alias("source"))
    return df


def collect_ports(tracker: SourceTracker | None = None) -> int:
    """Collect the UN/LOCODE reference into ports.

    Annual/manual cadence — the list is released biannually and there are no
    rate limits, but the download is ~13.5 MB so it is run politely.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, "unlocode_ports") as tc:
        zip_bytes = download_zip()
        df = parse_code_lists(zip_bytes)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No UN/LOCODE reference data returned")
            return 0

        logger.info("Writing %d UN/LOCODE reference records", df.height)
        count = write_raw(SOURCE, df, table_name="ports")
        tc.rows_written = count
        return count
