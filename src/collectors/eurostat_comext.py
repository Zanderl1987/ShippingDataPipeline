"""Collect EU international trade flow data from Eurostat Comext (SDMX 2.1).

Comext's SDMX API (``ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1``)
is keyless. Dataset ``DS-045409`` ("EU trade since 1988 by HS2-4-6 and CN8")
is queried at annual frequency with ``product=TOTAL``, wildcarding ``partner``
so one call returns a reporter country's trade with every partner at once
(live-verified: DE, all partners, one year -> 234 series in one request).

The 6-dimension SDMX key order (``freq.reporter.partner.product.flow.
indicators``) comes from the dataset's own DSD, not from docs -- a query
missing a dimension 400s with ``INVALID_QUERY_NB_FILTERS`` rather than
telling you which one. ``flow`` uses codelist ``CXT_EU_FLUX`` (1=import,
2=export, 3=re-export), remapped here to Comtrade's M/X/RX letters so both
sources share one ``flow_code`` convention in ``trade_flow``.

Comext reports values in EUR, not USD like UN Comtrade's ``trade_value_usd``
column -- rather than mislabel one as the other, rows carry an explicit
``currency`` column (see migration ``202608280001``).
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any
from xml.etree import ElementTree as ET

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1"
DATASET_ID = "DS-045409"
SOURCE = "eurostat_comext"

# CXT_EU_FLUX codelist, remapped to UN Comtrade's flow_code letters so both
# sources share one convention in the shared trade_flow table.
FLOW_CODES = {"import": "1", "export": "2", "re-export": "3"}
FLOW_TO_LETTER = {"1": "M", "2": "X", "3": "RX"}

_SDMX_NS = {
    "m": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/message",
    "g": "http://www.sdmx.org/resources/sdmxml/schemas/v2_1/data/generic",
}


def fetch_comext_data(
    *,
    reporter: str,
    partner: str = "",
    product: str = "TOTAL",
    flow: str = "2",
    freq: str = "A",
    start_period: str,
    end_period: str,
) -> str:
    """Fetch raw SDMX-XML for one Comext series key.

    ``partner`` left blank wildcards every partner country in one request.
    """
    key = f"{freq}.{reporter}.{partner}.{product}.{flow}.VALUE_IN_EUROS"
    url = f"{BASE_URL}/data/{DATASET_ID}/{key}"
    params = {"startPeriod": start_period, "endPeriod": end_period}
    logger.info(
        "Fetching Eurostat Comext: reporter=%s partner=%s flow=%s freq=%s",
        reporter, partner or "ALL", flow, freq,
    )
    resp = requests.get(url, params=params, timeout=60)
    resp.raise_for_status()
    return resp.text


def _parse_comext_xml(xml_text: str) -> list[dict[str, Any]]:
    """Flatten Comext's SDMX-generic XML into one record per observation."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        logger.warning("Comext response was not valid XML")
        return []

    g = _SDMX_NS["g"]
    records: list[dict[str, Any]] = []
    for series in root.iter(f"{{{g}}}Series"):
        key_el = series.find(f"{{{g}}}SeriesKey")
        if key_el is None:
            continue
        dims = {
            v.get("id"): v.get("value")
            for v in key_el.findall(f"{{{g}}}Value")
        }
        flow_raw = dims.get("flow") or ""
        for obs in series.findall(f"{{{g}}}Obs"):
            dim_el = obs.find(f"{{{g}}}ObsDimension")
            val_el = obs.find(f"{{{g}}}ObsValue")
            if dim_el is None or val_el is None:
                continue
            time_period = dim_el.get("value")
            value = val_el.get("value")
            if not time_period or value is None:
                continue
            records.append({
                "reporter_code": dims.get("reporter"),
                "partner_code": dims.get("partner"),
                "commodity_code": dims.get("product"),
                "flow_code": FLOW_TO_LETTER.get(flow_raw, flow_raw),
                "time_period": time_period,
                "trade_value_eur": float(value),
            })
    return records


def parse_comext_records(xml_text: str) -> pl.DataFrame:
    """Parse Comext SDMX-XML into a trade_flow-shaped Polars DataFrame."""
    records = _parse_comext_xml(xml_text)
    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records).with_columns(
        # Annual frequency (freq=A) returns a plain 4-digit year string.
        pl.col("time_period").cast(pl.Int32, strict=False).alias("year"),
        pl.col("trade_value_eur").alias("trade_value_usd"),
        pl.lit("EUR").alias("currency"),
        pl.lit(SOURCE).alias("source"),
    ).drop(["time_period", "trade_value_eur"])
    return df


def collect_comext_data(
    *,
    reporter: str = "DE",
    partner: str = "",
    flows: tuple[str, ...] = ("1", "2"),
    start_period: str | None = None,
    end_period: str | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect Eurostat Comext trade flow data and write to storage.

    Defaults to the last 5 years, both import and export flow, ``TOTAL``
    product, every partner (wildcarded) for one reporter country.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    if end_period is None:
        end_period = str(date.today().year)
    if start_period is None:
        start_period = str(date.today().year - 5)

    with TimedCollector(tracker, SOURCE) as tc:
        frames: list[pl.DataFrame] = []
        for flow in flows:
            xml_text = fetch_comext_data(
                reporter=reporter,
                partner=partner,
                flow=flow,
                start_period=start_period,
                end_period=end_period,
            )
            frame = parse_comext_records(xml_text)
            if frame.height:
                frames.append(frame)

        df = pl.concat(frames, how="vertical_relaxed") if frames else pl.DataFrame()
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No Eurostat Comext data returned for reporter=%s", reporter)
            return 0

        logger.info("Writing %d Eurostat Comext records for reporter=%s", df.height, reporter)
        count = write_raw(SOURCE, df, table_name="trade_flow")
        tc.rows_written = count
        return count
