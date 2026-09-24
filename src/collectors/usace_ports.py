"""USACE principal US ports: annual cargo tonnage (Waterborne Commerce summary).

The Waterborne Commerce Statistics Center's detailed data sits behind a login
(navigationdatacenter.us), but USACE's Institute for Water Resources publishes
the principal-ports summary as a public ArcGIS layer: ~150 ports with total,
domestic, foreign, import and export short tons for one calendar year. The
layer is replaced when a new year is released, and says which year in its
description ("... for CY 2023."), so collecting it regularly builds a yearly
series from here on; earlier years are not in the layer.

License: "publicly available without any use restrictions" (item metadata,
checked 2026-09-24).
"""
from __future__ import annotations

import logging
import re
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.collectors.portwatch_ports import query_all
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

SOURCE = "usace_principal_ports"
TABLE = "port_tonnage_us"
LAYER_URL = (
    "https://services7.arcgis.com/n1YM8pTrFmm7L4hs/arcgis/rest/services/"
    "Principal_Ports/FeatureServer/0"
)

_YEAR = re.compile(r"\bCY\s*(\d{4})\b")

_FIELDS: list[tuple[str, str, pl.DataType]] = [
    ("port_code", "PORT", pl.Utf8()),
    ("port_name", "PORTNAME", pl.Utf8()),
    ("port_type", "TYPE", pl.Utf8()),
    ("tonnage_rank", "RANK", pl.Int32()),
    ("total_tons", "TOTAL", pl.Float64()),
    ("domestic_tons", "DOMESTIC", pl.Float64()),
    ("foreign_tons", "FOREIGN_", pl.Float64()),
    ("import_tons", "IMPORTS", pl.Float64()),
    ("export_tons", "EXPORTS", pl.Float64()),
]


def data_year(description: str) -> int:
    """The calendar year the layer covers, from its description."""
    match = _YEAR.search(description or "")
    if not match:
        # Without the year the rows can't be keyed; a silent guess would
        # overwrite last year's figures with this year's under the wrong label.
        raise ValueError(f"No 'CY <year>' in the USACE layer description: {description!r}")
    return int(match.group(1))


def _fetch_description() -> str:
    data = get_with_retry(LAYER_URL, params={"f": "json"}, timeout=30, source=SOURCE).json()
    if "error" in data:
        raise RuntimeError(f"ArcGIS layer info failed: {data['error']}")
    return str(data.get("description") or data.get("serviceDescription") or "")


def _code(value: Any) -> str | None:
    # PORT arrives as a double (2031.0); the code is a 4-digit identifier.
    if value is None:
        return None
    return str(int(value)).zfill(4)


def parse(features: list[dict[str, Any]], year: int) -> pl.DataFrame:
    rows = []
    for feat in features:
        attrs = feat.get("attributes", {})
        row = {name: attrs.get(field) for name, field, _ in _FIELDS}
        row["port_code"] = _code(attrs.get("PORT"))
        centroid = feat.get("centroid") or {}
        row["latitude"] = centroid.get("y")
        row["longitude"] = centroid.get("x")
        rows.append(row)
    schema = {name: dtype for name, _, dtype in _FIELDS} | {
        "latitude": pl.Float64(),
        "longitude": pl.Float64(),
    }
    return (
        pl.DataFrame(rows, schema=schema, strict=False)
        .filter(pl.col("port_code").is_not_null())
        .with_columns(
            pl.lit(year).cast(pl.Int32).alias("data_year"),
            pl.lit(SOURCE).alias("source"),
        )
    )


def collect_principal_ports(tracker: SourceTracker | None = None) -> int:
    """Collect the current year's principal-port tonnage into `port_tonnage_us`."""
    if tracker is None:
        tracker = SourceTracker()
    with TimedCollector(tracker, SOURCE) as tc:
        year = data_year(_fetch_description())
        features = query_all(LAYER_URL + "/query", return_centroid=True, source=SOURCE)
        df = parse(features, year)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No USACE principal ports returned")
            return 0
        logger.info("Writing %d USACE principal ports for CY %d", df.height, year)
        tc.rows_written = write_raw(SOURCE, df, table_name=TABLE)
        return tc.rows_written
