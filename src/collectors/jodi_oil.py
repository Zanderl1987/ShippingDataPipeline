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

BASE_URL = "https://www.jodidata.org/oil/files"
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
    """Download CSV content from URL."""
    logger.info("Downloading JODI data from %s", url)
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    return resp.text


def get_primary_data() -> str:
    """Download JODI-Oil primary products CSV (crude oil, NGL, other)."""
    url = f"{BASE_URL}/Extended_Primary_CSV.csv"
    return download_csv(url)


def get_secondary_data() -> str:
    """Download JODI-Oil secondary products CSV (oil products)."""
    url = f"{BASE_URL}/Extended_Secondary_CSV.csv"
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
        product_code = row.get("PRODUCT", "")
        if products and product_code not in products:
            continue

        flow_code = row.get("FLOW_BREAKDOWN", "")
        flow_name = FLOW_MAP.get(flow_code, flow_code)

        quantity_str = row.get("DATAVALUE", "0")
        try:
            quantity = float(quantity_str.replace(",", "")) if quantity_str else 0.0
        except (ValueError, AttributeError):
            quantity = 0.0

        unit = row.get("UNIT", "KTONS")

        period = row.get("TIME_PERIOD", "")
        reporting = row.get("REPORTING_COUNTRY", "")
        partner = row.get("PARTNER_COUNTRY", "")

        records.append({
            "period": period,
            "reporting_country": reporting,
            "reporting_code": 0,
            "partner_country": partner,
            "partner_code": 0,
            "product": PRODUCT_MAP.get(product_code, product_code),
            "product_code": product_code,
            "flow": flow_name,
            "quantity_ktonnes": quantity if unit == "KTONS" else None,
            "quantity_barrels": quantity if unit in ("KBBL", "KB") else None,
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
