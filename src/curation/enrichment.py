"""Enrichment and curated table creation."""
from __future__ import annotations

import logging

import duckdb
import polars as pl

from src.storage.writer import get_db_path

logger = logging.getLogger(__name__)


def _get_count(conn: duckdb.DuckDBPyConnection, sql: str) -> int:
    """Execute a count query and return the result."""
    row = conn.execute(sql).fetchone()
    assert row is not None
    result: int = row[0]
    return result


def enrich_ais_with_vessel_info(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Enrich AIS positions with vessel metadata from vessels table.

    Returns DataFrame with AIS positions joined with vessel info.
    """
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        result = conn.execute("""
            SELECT
                a.mmsi,
                a.imo,
                a.vessel_name,
                a.latitude,
                a.longitude,
                a.sog,
                a.cog,
                a.heading,
                a.nav_status,
                a.draught,
                a.destination,
                a.eta,
                a.timestamp,
                a.source,
                a.partition_date,
                v.vessel_type,
                v.flag,
                v.callsign,
                v.length_m,
                v.beam_m,
                v.gross_tonnage,
                v.deadweight_tonnage,
                v.year_built
            FROM ais_positions a
            LEFT JOIN vessels v ON a.imo = v.imo
        """).pl()

        logger.info(
            "Enriched %d AIS positions with vessel info", result.height
        )
        return result
    finally:
        if should_close:
            conn.close()


def enrich_ais_with_port_info(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> pl.DataFrame:
    """Enrich AIS positions with destination port info.

    Returns DataFrame with AIS positions joined with port info for destination.
    """
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        result = conn.execute("""
            SELECT
                a.*,
                p.port_name as destination_port_name,
                p.country as destination_country,
                p.latitude as destination_latitude,
                p.longitude as destination_longitude
            FROM ais_positions a
            LEFT JOIN ports p ON a.destination = p.unlocode
        """).pl()

        logger.info(
            "Enriched %d AIS positions with port info", result.height
        )
        return result
    finally:
        if should_close:
            conn.close()


def create_curated_ais_positions(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Create curated_ais_positions table with enriched data.

    Returns number of rows written.
    """
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        conn.execute("DROP TABLE IF EXISTS curated_ais_positions")
        conn.execute("""
            CREATE TABLE curated_ais_positions AS
            SELECT
                a.mmsi,
                a.imo,
                a.vessel_name,
                a.latitude,
                a.longitude,
                a.sog,
                a.cog,
                a.heading,
                a.nav_status,
                a.draught,
                a.destination,
                a.eta,
                a.timestamp,
                a.source,
                a.partition_date,
                v.vessel_type,
                v.flag,
                v.callsign,
                v.length_m,
                v.beam_m,
                v.gross_tonnage,
                v.deadweight_tonnage,
                v.year_built,
                p.port_name as destination_port_name,
                p.country as destination_country
            FROM ais_positions a
            LEFT JOIN vessels v ON a.imo = v.imo
            LEFT JOIN ports p ON a.destination = p.unlocode
        """)

        count = _get_count(conn, "SELECT count(*) FROM curated_ais_positions")

        logger.info("Created curated_ais_positions with %d rows", count)
        return count
    finally:
        if should_close:
            conn.close()


def create_curated_vessels(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Create curated_vessels table with additional computed fields.

    Returns number of rows written.
    """
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        conn.execute("DROP TABLE IF EXISTS curated_vessels")
        conn.execute("""
            CREATE TABLE curated_vessels AS
            SELECT
                imo,
                mmsi,
                vessel_name,
                vessel_type,
                flag,
                callsign,
                length_m,
                beam_m,
                gross_tonnage,
                deadweight_tonnage,
                year_built,
                owner_name,
                manager_name,
                source,
                ingested_at,
                EXTRACT(YEAR FROM CURRENT_DATE) - year_built as vessel_age
            FROM vessels
        """)

        count = _get_count(conn, "SELECT count(*) FROM curated_vessels")

        logger.info("Created curated_vessels with %d rows", count)
        return count
    finally:
        if should_close:
            conn.close()
