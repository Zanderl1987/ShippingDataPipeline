"""Schema drift detection for collected data.

Compares incoming column sets against expected table schemas and alerts
on unexpected changes (new columns, missing columns).

Usage:
    from src.monitoring.schema_drift import check_schema_drift

    result = check_schema_drift("ais_positions", df)
    if result["drifted"]:
        logger.warning("Schema drift: %s", result)
"""
from __future__ import annotations

import logging
from typing import Any

import polars as pl

from src.storage.schema import ALL_TABLES

logger = logging.getLogger(__name__)


def get_expected_columns(table_name: str) -> set[str]:
    """Extract column names from a table's CREATE TABLE SQL.

    Parses the ``raw_sql`` of each :class:`TableSchema` in
    :data:`ALL_TABLES` and returns the set of lowercased column names.

    Args:
        table_name: The table name to look up (e.g. ``"ais_positions"``).

    Returns:
        Set of column names, or an empty set if the table is not found.
    """
    for table in ALL_TABLES:
        if table.name == table_name:
            columns: set[str] = set()
            in_create = False
            for line in table.raw_sql.split("\n"):
                stripped = line.strip()
                upper = stripped.upper()

                if "CREATE TABLE" in upper:
                    in_create = True
                    continue

                if in_create and stripped.startswith(")"):
                    break

                if in_create and stripped and not stripped.startswith("--"):
                    col_name = stripped.split()[0].strip(",").strip('"')
                    skip = {"CREATE", "TABLE", "IF", "NOT", "EXISTS", ""}
                    if col_name.upper() not in skip:
                        columns.add(col_name.lower())
            return columns

    logger.debug("Table %r not found in ALL_TABLES — returning empty set", table_name)
    return set()


def check_schema_drift(
    table_name: str,
    df: pl.DataFrame,
) -> dict[str, Any]:
    """Check if a DataFrame's columns drift from the expected schema.

    Args:
        table_name: Name of the table to validate against.
        df: Polars DataFrame with the incoming data.

    Returns:
        Dict with:
        - ``table``: the table name
        - ``unexpected_columns``: columns present in *df* but not in the schema
        - ``missing_columns``: columns in the schema but absent from *df*
        - ``drifted``: True if any drift is detected
    """
    expected = get_expected_columns(table_name)
    actual = set(df.columns)

    unexpected = actual - expected
    missing = expected - actual

    drifted = bool(unexpected or missing)

    if drifted:
        logger.warning(
            "Schema drift detected for %s: unexpected=%s missing=%s",
            table_name,
            sorted(unexpected),
            sorted(missing),
        )

    return {
        "table": table_name,
        "unexpected_columns": sorted(unexpected),
        "missing_columns": sorted(missing),
        "drifted": drifted,
    }
