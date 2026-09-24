"""Null out impossible values and drop unusable rows before validation.

AIS encodes "not available" as an out-of-range number (ITU-R M.1371): speed
102.3 kn, course 360, heading 511, latitude 91, longitude 181. Collectors pass
them through, so without this step they read as real readings (a ship doing
102.3 knots). This runs on the whole table every curation run, so rows already
stored -- including history seeded from HuggingFace -- are fixed too, and the
cleaned table is what gets published.
"""
from __future__ import annotations

import logging

import duckdb

logger = logging.getLogger(__name__)

#: Sources that never report mmsi or a timestamp (see AIS_POSITIONS in
#: schema.py); rows from anywhere else without them can't be placed in time
#: or tied to a vessel.
NO_MMSI_OR_TIMESTAMP_SOURCES = ("axiomancer",)

#: Valid value for each AIS column, as a SQL condition. Anything else is NULLed.
AIS_VALID = {
    "latitude": "latitude BETWEEN -90 AND 90",
    "longitude": "longitude BETWEEN -180 AND 180",
    # 102.2 means "102.2 kn or more"; 102.3 means not available.
    "sog": "sog BETWEEN 0 AND 102.2",
    # 360 means not available.
    "cog": "cog >= 0 AND cog < 360",
    # 511 means not available.
    "heading": "heading BETWEEN 0 AND 359",
}

_PORT_COORDS_INVALID = (
    "NOT coalesce(latitude BETWEEN -90 AND 90, true) "
    "OR NOT coalesce(longitude BETWEEN -180 AND 180, true)"
)


def _changed(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    row = conn.execute(sql).fetchone()
    return int(row[0]) if row else 0


def clean_ais_positions(conn: duckdb.DuckDBPyConnection) -> int:
    """NULL out-of-range AIS values and delete rows missing mmsi or timestamp.

    Returns:
        Number of values nulled plus rows deleted.
    """
    changed = 0
    for column, valid in AIS_VALID.items():
        n = _changed(
            conn,
            f"UPDATE ais_positions SET {column} = NULL "
            f"WHERE {column} IS NOT NULL AND NOT ({valid})",
        )
        if n:
            logger.info("ais_positions: nulled %d invalid %s values", n, column)
        changed += n

    exempt = ", ".join(f"'{s}'" for s in NO_MMSI_OR_TIMESTAMP_SOURCES)
    n = _changed(
        conn,
        f"DELETE FROM ais_positions WHERE source NOT IN ({exempt}) "
        "AND (mmsi IS NULL OR timestamp IS NULL)",
    )
    if n:
        logger.info("ais_positions: deleted %d rows without mmsi or timestamp", n)
    return changed + n


def clean_ports(conn: duckdb.DuckDBPyConnection) -> int:
    """NULL both coordinates of any port where either is out of range.

    UN/LOCODE's own file has typos (Mironovka UA at longitude 381.8); when one
    half of the pair is impossible the other can't be trusted either.

    Returns:
        Number of ports changed.
    """
    n = _changed(
        conn,
        f"UPDATE ports SET latitude = NULL, longitude = NULL WHERE {_PORT_COORDS_INVALID}",
    )
    if n:
        logger.info("ports: nulled coordinates of %d ports", n)
    return n
