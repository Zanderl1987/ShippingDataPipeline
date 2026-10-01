"""Collect USDA's monthly WASDE report, every line as published at the time.

The World Agricultural Supply and Demand Estimates (WASDE) is USDA's monthly
balance sheet for grains, oilseeds, cotton, sugar, meat and dairy: production,
use, trade and stocks for the US and the world, each a projection for the
current season and an estimate for the one before. Each report is kept as
released, so this table holds every vintage, not just the latest revision.
That is what a fair benchmark needs: what USDA expected at the time.

Source (live-checked 2026-10-01): USDA's ESMIS library at the National
Agricultural Library. Its JSON API lists every WASDE release with its files,
and each release since September 2010 has an XML copy of the full report.

usda.gov's own CSV copies were tried first, but usda.gov's CDN returns 403 to
GitHub's runners even with browser headers, so CI could not load them. ESMIS
answers plain scripted requests. The XML labels differ in places from the CSV
copies (those are hand-curated), but the world supply-and-use tables, including
the US export lines the grain forecast uses, carry the same titles, commodities
and values. The reliability tables use a different layout and are left out.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from typing import Any

import duckdb
import polars as pl
import requests

from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import get_connection, write_raw

logger = logging.getLogger(__name__)

SOURCE = "usda_wasde"
TABLE = "usda_wasde"
RELEASES_URL = "https://esmis.nal.usda.gov/api/v1/release/findByIdentifier/wasde"
#: The first release with an XML copy (earlier ones are PDF and text only).
FIRST_RELEASE = date(2010, 9, 1)
#: Releases re-fetched on every run, for corrected reissues.
RECENT_RELEASES = 3
HEADERS = {"User-Agent": "ShippingDataPipeline/1.0 (+https://github.com/Zanderl1987/ShippingDataPipeline)"}
TIMEOUT = 120

#: Labels read from each value's ancestors (the XML adds an ``m1_``-style prefix
#: and a numeric suffix per table, e.g. ``m1_attribute_group2/attribute4``).
_KEYS = (
    "commodity", "commodity_header", "attribute_group", "region", "region_header",
    "market_year",
    "forecast_month", "attribute", "unit_descr", "unit_description",
)
#: US quarterly tables use the forecast-month slot for the quarter.
_PERIODS = {"Annual", "I", "II", "III", "III*", "IV"}
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
#: Tables about one commodity whose rows don't name it, labelled as usda.gov's CSV does.
_TITLE_COMMODITY = {
    "World Corn Supply and Use": "Corn",
    "World Soybean Supply and Use": "Oilseed, Soybean",
    "World Soybean Meal Supply and Use": "Soybean Meal",
    "World Soybean Oil Supply and Use": "Soybean Oil",
    "World Wheat Supply and Use": "Wheat",
    "World Coarse Grain Supply and Use": "Coarse Grain",
    "World Rice Supply and Use (Milled Basis)": "Rice",
    "World Cotton Supply and Use": "Cotton",
    "World and U.S. Supply and Use for Cotton": "Cotton",
    "U.S. Wheat Supply and Use": "Wheat",
    "U.S. Cotton Supply and Use": "Cotton",
    "U.S. Sugar Supply and Use": "Sugar",
    "U.S. Egg Supply and Use": "Eggs",
}
_FOOTNOTE = re.compile(r"\s+\d+/(?=\s|$)")
_YEAR = re.compile(r"^(\d{4}(?:/\d{2})?)\s*\(?\s*(Proj\.|Est\.)?\s*\)?$")


def _clean(text: str | None) -> str | None:
    """Collapse whitespace and drop footnote marks like ``2/``."""
    if text is None:
        return None
    text = _FOOTNOTE.sub("", " " + " ".join(text.split())).strip()
    return text or None


def _title(raw: str) -> str:
    title = _clean(raw.replace("(Cont'd.)", "").replace("(Cont'd)", "")) or ""
    return title


def _label(key: str) -> str:
    return re.sub(r"\d+$", "", re.sub(r"^m\d_", "", key))


def _walk(el: ET.Element, ctx: dict[str, str], report: dict[str, str],
          out: list[dict[str, Any]]) -> None:
    if "sub_report_title" in el.attrib:
        report = {
            "report_title": _title(el.get("sub_report_title", "")),
            "unit": (_clean(el.get("sub_report_subtitle")) or "").strip("()").strip(),
            "page": el.get("page_title", ""),
        }
        ctx = {}
    else:
        ctx = dict(ctx)
    for key, value in el.attrib.items():
        label = _label(key)
        if label in _KEYS:
            ctx[label] = value
        elif label == "cell_value":
            out.append({**report, **ctx, "raw": value})
    for child in el:
        _walk(child, ctx, report, out)


def parse_wasde_xml(data: bytes, release_date: date) -> pl.DataFrame:
    """Normalize one WASDE XML report into ``usda_wasde`` columns.

    A projection shown for both last month and this month keeps only this
    month's figure, as usda.gov's CSV does.
    """
    cells: list[dict[str, Any]] = []
    _walk(ET.fromstring(data), {}, {}, cells)
    rows = []
    for c in cells:
        title = c.get("report_title") or ""
        if not title or title.startswith("Reliability"):
            continue
        month = (c.get("forecast_month") or "").strip()
        period = "Annual"
        if month in _PERIODS:
            period = month
        elif month and _MONTHS.get(month[:3].lower()) != release_date.month:
            continue  # last month's projection, shown for comparison
        year_text = " ".join((c.get("market_year") or c.get("region_header") or "").split())
        match = _YEAR.match(year_text)
        market_year, flag = (match.group(1), match.group(2)) if match else (year_text or None, None)
        unit = _clean(c.get("unit_descr") or c.get("unit_description")) or c.get("unit") or None
        commodity = (_clean(c.get("commodity")) or _clean(c.get("attribute_group"))
                     or _TITLE_COMMODITY.get(title))
        number = re.search(r"WASDE\s*-\s*(\d+)", c.get("page") or "")
        raw = (c.get("raw") or "").replace(",", "").strip()
        rows.append({
            "wasde_number": int(number.group(1)) if number else None,
            "report_title": title,
            "attribute": _clean(c.get("attribute")),
            "reliability_projection": None,
            "commodity": commodity,
            "region": _clean(c.get("region") or c.get("commodity_header")),
            "market_year": market_year,
            "proj_est_flag": flag,
            "period": period,
            "value": raw,
            "unit": unit,
        })
    schema = {
        "wasde_number": pl.Int32, "report_title": pl.Utf8, "attribute": pl.Utf8,
        "reliability_projection": pl.Utf8, "commodity": pl.Utf8, "region": pl.Utf8,
        "market_year": pl.Utf8, "proj_est_flag": pl.Utf8, "period": pl.Utf8,
        "value": pl.Utf8, "unit": pl.Utf8,
    }
    df = pl.DataFrame(rows, schema=schema).with_columns(
        pl.col("value").cast(pl.Float64, strict=False),
        pl.lit(release_date).alias("release_date"),
        pl.lit(release_date.year).cast(pl.Int32).alias("release_year"),
        pl.lit(SOURCE).alias("source"),
    )
    keys = ["report_title", "attribute", "commodity", "region", "market_year", "period", "unit"]
    return df.filter(pl.col("attribute").is_not_null()).unique(keys, keep="first",
                                                                maintain_order=True)


def list_releases(*, since: date | None = None) -> list[tuple[date, str]]:
    """(release date, XML url) for each WASDE release, newest first.

    With ``since``, reads every page of the ESMIS listing: it is mostly newest
    first, but some old releases are filed among 2018-2020 ones, so stopping at
    the first old date would miss releases. Without it, reads only the first
    page (the latest 25). A date listed more than once (2019-11-08 is listed
    three times, all the same report) is kept once.
    """
    out: list[tuple[date, str]] = []
    seen: set[date] = set()
    page, pages = 0, 1
    while page < pages:
        resp = requests.get(RELEASES_URL, params={"page": page}, headers=HEADERS,
                            timeout=TIMEOUT)
        resp.raise_for_status()
        body = resp.json()
        for r in body.get("results") or []:
            released = datetime.strptime(r["release_datetime"][:10], "%Y-%m-%d").date()
            xml = [f for f in r.get("files") or [] if f.lower().endswith(".xml")]
            if xml and (since is None or released >= since) and released not in seen:
                seen.add(released)
                out.append((released, xml[0]))
        if since is not None:
            pages = int((body.get("pager") or {}).get("total_pages") or 0)
        page += 1
    out.sort(reverse=True)
    return out


def fetch_release(released: date, url: str) -> pl.DataFrame:
    resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    resp.raise_for_status()
    df = parse_wasde_xml(resp.content, released)
    logger.info("WASDE %s: %d rows", released, df.height)
    return df


def _stored_releases() -> dict[date, int | None]:
    """Report number of each stored release date."""
    conn = get_connection()
    try:
        rows = conn.execute(
            f"SELECT release_date, max(wasde_number) FROM {TABLE} WHERE source = ? "
            "GROUP BY release_date",
            [SOURCE],
        ).fetchall()
    except duckdb.CatalogException:
        return {}
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows}


def collect_wasde(
    *,
    tracker: SourceTracker | None = None,
    bulk_backfill: bool | None = None,
) -> int:
    """Collect WASDE reports into ``usda_wasde``.

    Every run re-fetches the latest ``RECENT_RELEASES`` reports. When bulk
    backfill is allowed (CI), every report since September 2010 that is not
    stored yet is filled in.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()
    if bulk_backfill is None:
        bulk_backfill = settings.allow_bulk_backfill

    with TimedCollector(tracker, SOURCE) as tc:
        if bulk_backfill:
            releases = list_releases(since=FIRST_RELEASE)
            stored = _stored_releases()
            recent = {d for d, _ in releases[:RECENT_RELEASES]}
            releases = [(d, u) for d, u in releases if d not in stored or d in recent]
        else:
            releases = list_releases()[:RECENT_RELEASES]
        # Report number -> first release date: ESMIS also lists a second copy of
        # December 2018's report three days late, which is not a new vintage.
        first: dict[int, date] = {}
        for released, number in sorted(_stored_releases().items()):
            if number is not None:
                first.setdefault(number, released)
        written = 0
        for released, url in sorted(releases):
            df = fetch_release(released, url)
            report = df["wasde_number"].drop_nulls().max() if df.height else None
            if isinstance(report, int) and first.setdefault(report, released) < released:
                logger.info("WASDE %s: copy of report %d, skipped", released, report)
                continue
            tc.rows_fetched += df.height
            written += write_raw(SOURCE, df, table_name=TABLE)
        tc.rows_written = written
        return written
