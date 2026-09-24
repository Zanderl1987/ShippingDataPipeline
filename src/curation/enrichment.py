"""Enrichment and curated table creation."""
from __future__ import annotations

import logging

import duckdb
import polars as pl

from src.storage.writer import get_db_path

logger = logging.getLogger(__name__)

#: One row per port code from ``ports``, which holds a row per source. Each
#: field comes from the most trusted source that has it (UN/LOCODE, then
#: Digitraffic, then anything else); latitude and longitude are taken as a
#: pair so they always come from the same row.
PORTS_BY_CODE = """(
    SELECT
        unlocode,
        arg_min(port_name, rank) FILTER (WHERE port_name IS NOT NULL) AS port_name,
        arg_min(country, rank) FILTER (WHERE country IS NOT NULL) AS country,
        arg_min(country_code, rank) FILTER (WHERE country_code IS NOT NULL) AS country_code,
        arg_min(latitude, rank)
            FILTER (WHERE latitude IS NOT NULL AND longitude IS NOT NULL) AS latitude,
        arg_min(longitude, rank)
            FILTER (WHERE latitude IS NOT NULL AND longitude IS NOT NULL) AS longitude
    FROM (
        SELECT *,
            CASE source WHEN 'unlocode' THEN 0 WHEN 'digitraffic' THEN 1 ELSE 2 END AS rank
        FROM ports
    )
    GROUP BY unlocode
)"""


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
        result = conn.execute(f"""
            SELECT
                a.*,
                p.port_name as destination_port_name,
                p.country as destination_country,
                p.latitude as destination_latitude,
                p.longitude as destination_longitude
            FROM ais_positions a
            LEFT JOIN {PORTS_BY_CODE} p ON a.destination = p.unlocode
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
        conn.execute(f"""
            CREATE OR REPLACE TABLE curated_ais_positions AS
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
            LEFT JOIN {PORTS_BY_CODE} p ON a.destination = p.unlocode
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


def create_port_congestion_proxy(
    conn: duckdb.DuckDBPyConnection | None = None,
) -> int:
    """Create port_congestion_proxy: each port's latest 7-day average port calls
    against its 90-day average, from port_activity (IMF PortWatch).

    This is the metric FreightPulse's /port-congestion endpoint serves since its
    2026-09 API change; its figures match this computation exactly (the 90-day
    window includes the recent 7 days). It measures activity against a
    baseline, not vessel waiting time. One row per port, at that port's latest
    date, so the table stays small; history is recomputable from port_activity.

    Returns number of rows written.
    """
    should_close = False
    if conn is None:
        db_path = get_db_path()
        conn = duckdb.connect(str(db_path))
        should_close = True

    try:
        conn.execute("""
            CREATE OR REPLACE TABLE port_congestion_proxy AS
            WITH latest AS (
                SELECT port_id, max(activity_date) AS as_of
                FROM port_activity
                GROUP BY port_id
            ),
            windowed AS (
                SELECT
                    a.port_id,
                    any_value(a.port_name) AS port_name,
                    any_value(a.country) AS country,
                    l.as_of,
                    avg(a.portcalls) FILTER (WHERE a.activity_date > l.as_of - 7)
                        AS recent_avg_portcalls_per_day,
                    avg(a.portcalls) AS baseline_avg_portcalls_per_day,
                    count(a.portcalls) AS baseline_days
                FROM port_activity a
                JOIN latest l ON a.port_id = l.port_id
                WHERE a.activity_date > l.as_of - 90
                GROUP BY a.port_id, l.as_of
            )
            SELECT
                w.port_id,
                w.port_name,
                p.locode,
                w.country,
                w.as_of,
                w.recent_avg_portcalls_per_day,
                w.baseline_avg_portcalls_per_day,
                w.recent_avg_portcalls_per_day
                    / nullif(w.baseline_avg_portcalls_per_day, 0) AS ratio_vs_baseline,
                w.baseline_days
            FROM windowed w
            LEFT JOIN port_profiles p ON w.port_id = p.port_id
            ORDER BY w.port_id
        """)

        count = _get_count(conn, "SELECT count(*) FROM port_congestion_proxy")

        logger.info("Created port_congestion_proxy with %d rows", count)
        return count
    finally:
        if should_close:
            conn.close()
