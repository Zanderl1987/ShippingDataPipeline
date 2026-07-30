"""Collect global oil data from JODI-Oil."""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://www.jodidata.org/_resources/files/downloads/oil-data"
SOURCE = "jodi_oil"

PRODUCT_MAP = {
    "CRUDEOIL": "Crude oil",
    "NGL": "NGL",
    "OTHERCRUDE": "Other (refinery feedstocks + additives)",
    "TOTCRUDE": "Total primary",
    "LPG": "LPG",
    "NAPHTHA": "Naphtha",
    "GASOLINE": "Motor/aviation gasoline",
    "KEROSENE": "Kerosenes",
    "JETKERO": "Kerosene type jet fuel",
    "GASDIES": "Gas/diesel oil",
    "RESFUEL": "Fuel oil",
    "ONONSPEC": "Other oil products",
    "TOTPRODS": "Total oil products",
}

# Units that map onto the oil_trade schema's two quantity columns.
MASS_UNITS = frozenset({"KTONS"})
VOLUME_UNITS = frozenset({"KBBL", "KB", "CONVBBL"})

FLOW_MAP = {
    "INDPROD": "Production",
    "TOTIMPSB": "Imports",
    "TOTEXPSB": "Exports",
    "REFINOBS": "Refinery intake",
    "REFGROUT": "Refinery output",
    "CLOSTLV": "Closing stocks",
    "TOTDEMO": "Demand",
    "STOCKCH": "Stock change",
}


def download_csv(url: str) -> str:
    """Download JODI data from URL.

    JODI distributes the datasets as zipped CSVs; the archive holds a single
    CSV member which is returned as text.
    """
    logger.info("Downloading JODI data from %s", url)
    resp = requests.get(url, timeout=300)
    resp.raise_for_status()

    if url.endswith(".zip"):
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
            if not names:
                raise ValueError(f"No CSV member in JODI archive: {zf.namelist()}")
            with zf.open(names[0]) as member:
                return member.read().decode("utf-8", errors="replace")

    return resp.text


def get_primary_data() -> str:
    """Download JODI-Oil primary products CSV (crude oil, NGL, other)."""
    url = f"{BASE_URL}/world_primary_csv.zip"
    return download_csv(url)


def get_secondary_data() -> str:
    """Download JODI-Oil secondary products CSV (oil products)."""
    url = f"{BASE_URL}/world_secondary_csv.zip"
    return download_csv(url)


def _parse_jodi_csv(csv_text: str, products: list[str] | None = None) -> pl.DataFrame:
    """Parse JODI CSV into a DataFrame.

    Args:
        csv_text: Raw CSV text.
        products: Optional filter for specific product codes.

    Returns:
        DataFrame with oil trade data.
    """
    reader = csv.DictReader(io.StringIO(csv_text))

    records: list[dict[str, Any]] = []
    for row in reader:
        # JODI renamed its columns (PRODUCT -> ENERGY_PRODUCT, DATAVALUE ->
        # OBS_VALUE, UNIT -> UNIT_MEASURE, REPORTING_COUNTRY -> REF_AREA);
        # accept either spelling.
        product_code = row.get("ENERGY_PRODUCT") or row.get("PRODUCT", "")
        if products and product_code not in products:
            continue

        flow_code = row.get("FLOW_BREAKDOWN", "")
        flow_name = FLOW_MAP.get(flow_code, flow_code)

        quantity_str = row.get("OBS_VALUE") or row.get("DATAVALUE", "")
        # "-" (not available), "x" (confidential) and "N/A" are placeholders,
        # not zeroes — keep them null so they don't skew aggregates.
        try:
            quantity = (
                float(quantity_str.replace(",", "")) if quantity_str else None
            )
        except (ValueError, AttributeError):
            quantity = None

        unit = row.get("UNIT_MEASURE") or row.get("UNIT", "KTONS")

        period = row.get("TIME_PERIOD", "")
        reporting = row.get("REF_AREA") or row.get("REPORTING_COUNTRY", "")
        partner = row.get("PARTNER_COUNTRY", "")

        # The schema only carries mass (ktonnes) and volume (barrels). JODI also
        # publishes KBD (a rate) and KL (kilolitres), which map to neither
        # column, so those rows would persist with no value at all — skip them.
        if unit not in MASS_UNITS and unit not in VOLUME_UNITS:
            continue

        # A placeholder value means the country reported nothing for this
        # series. Persisting it stores a row with no measurement in it.
        if quantity is None:
            continue

        records.append({
            "period": period,
            "reporting_country": reporting,
            "reporting_code": 0,
            "partner_country": partner,
            "partner_code": 0,
            "product": PRODUCT_MAP.get(product_code, product_code),
            "product_code": product_code,
            "flow": flow_name,
            "quantity_ktonnes": quantity if unit in MASS_UNITS else None,
            "quantity_barrels": quantity if unit in VOLUME_UNITS else None,
            "unit": unit,
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)

    for col in ["quantity_ktonnes", "quantity_barrels"]:
        if col in df.columns:
            df = df.with_columns(pl.col(col).cast(pl.Float64, strict=False))

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_primary(
    products: list[str] | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect JODI-Oil primary products and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        csv_text = get_primary_data()
        df = _parse_jodi_csv(csv_text, products=products)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No JODI primary data returned")
            return 0

        logger.info("Writing %d JODI primary records", df.height)
        count = write_raw(SOURCE, df, table_name="oil_trade")
        tc.rows_written = count
        return count


def collect_secondary(
    products: list[str] | None = None,
    tracker: SourceTracker | None = None,
) -> int:
    """Collect JODI-Oil secondary products and write to storage.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        csv_text = get_secondary_data()
        df = _parse_jodi_csv(csv_text, products=products)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No JODI secondary data returned")
            return 0

        logger.info("Writing %d JODI secondary records", df.height)
        count = write_raw(SOURCE, df, table_name="oil_trade")
        tc.rows_written = count
        return count
