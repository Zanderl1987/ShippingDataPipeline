"""Alembic-style schema versioning for DuckDB."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import duckdb

from src.storage.writer import get_connection

logger = logging.getLogger(__name__)


@dataclass
class Migration:
    version: str
    description: str
    up_sql: str
    down_sql: str


# ── Registry ──────────────────────────────────────────────────────────────────
# Add new migrations here. Each gets a unique version string (use YYYYMMDDHHMM).

MIGRATIONS: list[Migration] = [
    Migration(
        version="20260727001",
        description="Add lineage_events table",
        up_sql="""
            CREATE TABLE IF NOT EXISTS lineage_events (
                event_id        INTEGER,
                event_type      VARCHAR NOT NULL,
                source          VARCHAR NOT NULL,
                started_at      TIMESTAMP NOT NULL,
                completed_at    TIMESTAMP,
                status          VARCHAR DEFAULT 'success',
                rows_input      INTEGER DEFAULT 0,
                rows_output     INTEGER DEFAULT 0,
                duration_ms     INTEGER,
                version         VARCHAR,
                config_hash     VARCHAR,
                error_message   VARCHAR,
                metadata        VARCHAR
            );
            CREATE INDEX IF NOT EXISTS idx_lineage_source ON lineage_events(source);
            CREATE INDEX IF NOT EXISTS idx_lineage_type ON lineage_events(event_type);
            CREATE INDEX IF NOT EXISTS idx_lineage_time ON lineage_events(started_at);
        """,
        down_sql="DROP TABLE IF EXISTS lineage_events;",
    ),
    Migration(
        version="20260727002",
        description="Add schema_versions tracking table",
        up_sql="""
            CREATE TABLE IF NOT EXISTS schema_versions (
                table_name      VARCHAR NOT NULL,
                version         VARCHAR NOT NULL,
                description     VARCHAR,
                applied_at      TIMESTAMP DEFAULT now(),
                rollback_sql    VARCHAR,
                PRIMARY KEY (table_name, version)
            );
        """,
        down_sql="DROP TABLE IF EXISTS schema_versions;",
    ),
    Migration(
        version="20260727003",
        description="Add vessel_type column to vessel_registry if missing",
        up_sql="ALTER TABLE vessel_registry ADD COLUMN IF NOT EXISTS vessel_type VARCHAR;",
        down_sql="",
    ),
    Migration(
        version="20260727004",
        description="Add collected_at timestamp to source_tracking",
        up_sql="ALTER TABLE source_tracking ADD COLUMN IF NOT EXISTS collected_at TIMESTAMP;",
        down_sql="",
    ),
    Migration(
        version="20260730001",
        description="Add vessel_type column to ais_positions if missing",
        # ais_positions predates vessel_type in schema.py. CREATE TABLE IF NOT
        # EXISTS never alters an existing table, so the column was silently
        # dropped from every write.
        up_sql="ALTER TABLE ais_positions ADD COLUMN IF NOT EXISTS vessel_type VARCHAR;",
        down_sql="",
    ),
    Migration(
        version="202608090001",
        description="Rebuild vessels without the stale imo PRIMARY KEY constraint",
        # The live vessels table still carries `imo BIGINT PRIMARY KEY` from an
        # old schema. Every vessel-writing collector (gfw, shiplookup, vesselapi,
        # seafarer_index) hit a ConstraintException on insert because many
        # vessels have no IMO, so the table stayed at 0 rows. DuckDB cannot
        # DROP CONSTRAINT, so rebuild the empty table to match schema.py
        # (dedup keys imo/mmsi/source, no PRIMARY KEY). Any rows already in the
        # table are preserved through the rebuild.
        up_sql="""
            CREATE OR REPLACE TABLE vessels_rebuild (
                imo             BIGINT,
                mmsi            BIGINT,
                vessel_name     VARCHAR,
                vessel_type     VARCHAR,
                flag            VARCHAR,
                callsign        VARCHAR,
                length_m        DOUBLE,
                beam_m          DOUBLE,
                gross_tonnage   DOUBLE,
                deadweight_tonnage DOUBLE,
                year_built      INTEGER,
                owner_name      VARCHAR,
                manager_name    VARCHAR,
                source          VARCHAR,
                ingested_at     TIMESTAMP DEFAULT now()
            );
            INSERT INTO vessels_rebuild SELECT * FROM vessels;
            DROP TABLE IF EXISTS vessels;
            ALTER TABLE vessels_rebuild RENAME TO vessels;
        """,
        down_sql="",
    ),
    Migration(
        version="202608100001",
        description="Add flag column to ais_positions if missing",
        # axiomancer reports each vessel's flag (registration country) on its
        # position feed, but ais_positions predates the flag column in
        # schema.py. CREATE TABLE IF NOT EXISTS never alters an existing table,
        # so the column was silently dropped from every write.
        up_sql="ALTER TABLE ais_positions ADD COLUMN IF NOT EXISTS flag VARCHAR;",
        down_sql="",
    ),
    Migration(
        version="202608260001",
        description="Add port_congestion table for FreightPulse data",
        up_sql="""
            CREATE TABLE IF NOT EXISTS port_congestion (
                snapshot_date           DATE,
                port_code               VARCHAR,
                port_name               VARCHAR,
                country                 VARCHAR,
                region                  VARCHAR,
                latitude                DOUBLE,
                longitude               DOUBLE,
                capacity_teu            DOUBLE,
                congestion_index        DOUBLE,
                congestion_level        VARCHAR,
                vessels_at_anchor       INTEGER,
                vessels_at_berth        INTEGER,
                avg_wait_time_hours     DOUBLE,
                avg_berth_time_hours    DOUBLE,
                container_dwell_days    DOUBLE,
                trend                   VARCHAR,
                change_week             INTEGER,
                source                  VARCHAR,
                partition_date          DATE,
                ingested_at             TIMESTAMP DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_pc_snapshot ON port_congestion(snapshot_date);
            CREATE INDEX IF NOT EXISTS idx_pc_port ON port_congestion(port_code);
        """,
        down_sql="DROP TABLE IF EXISTS port_congestion;",
    ),
    Migration(
        version="202608260002",
        description="Add fuel_prices table for marine bunker and road diesel prices",
        up_sql="""
            CREATE TABLE IF NOT EXISTS fuel_prices (
                snapshot_date           DATE,
                diesel_national_avg     DOUBLE,
                diesel_change_week      DOUBLE,
                diesel_east_coast       DOUBLE,
                diesel_midwest          DOUBLE,
                diesel_gulf_coast       DOUBLE,
                diesel_rocky_mountain   DOUBLE,
                diesel_west_coast       DOUBLE,
                diesel_california       DOUBLE,
                gasoline_regular        DOUBLE,
                gasoline_midgrade       DOUBLE,
                gasoline_premium        DOUBLE,
                gasoline_national_avg   DOUBLE,
                bunker_rotterdam        DOUBLE,
                bunker_singapore        DOUBLE,
                bunker_houston          DOUBLE,
                diesel_30d_avg          DOUBLE,
                diesel_90d_avg          DOUBLE,
                diesel_yoy_change       DOUBLE,
                source                  VARCHAR,
                partition_date          DATE,
                ingested_at             TIMESTAMP DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_fp_snapshot ON fuel_prices(snapshot_date);
        """,
        down_sql="DROP TABLE IF EXISTS fuel_prices;",
    ),
    Migration(
        version="202608260003",
        description="Add supply_chain_disruptions table for active disruption alerts",
        up_sql="""
            CREATE TABLE IF NOT EXISTS supply_chain_disruptions (
                snapshot_date           DATE,
                disruption_id           VARCHAR,
                disruption_type         VARCHAR,
                severity                VARCHAR,
                title                   VARCHAR,
                description             VARCHAR,
                affected_regions        VARCHAR,
                affected_routes         VARCHAR,
                transit_delay_days      INTEGER,
                rate_increase_pct       DOUBLE,
                capacity_reduction_pct  DOUBLE,
                started_at              VARCHAR,
                expected_resolution     VARCHAR,
                status                  VARCHAR,
                source                  VARCHAR,
                partition_date          DATE,
                ingested_at             TIMESTAMP DEFAULT now()
            );
            CREATE INDEX IF NOT EXISTS idx_scd_id ON supply_chain_disruptions(disruption_id);
            CREATE INDEX IF NOT EXISTS idx_scd_type ON supply_chain_disruptions(disruption_type);
        """,
        down_sql="DROP TABLE IF EXISTS supply_chain_disruptions;",
    ),
    Migration(
        version="202608240001",
        description="Add function_class and status columns to ports",
        # The UN/LOCODE collector (unlocode.py) loads the full UNECE code list
        # keyed by unlocode, carrying each location's function classifier
        # ('1' = port) and status code. ports predates both columns; without
        # this migration write_raw would silently drop them on every insert.
        up_sql="""
            ALTER TABLE ports ADD COLUMN IF NOT EXISTS function_class VARCHAR;
            ALTER TABLE ports ADD COLUMN IF NOT EXISTS status VARCHAR;
        """,
        down_sql="",
    ),
    Migration(
        version="202608260004",
        description="Add carriers table for FreightPulse carrier performance data",
        up_sql="""
            CREATE TABLE IF NOT EXISTS carriers (
                snapshot_date           DATE NOT NULL,
                carrier_name            VARCHAR,
                carrier_code            VARCHAR NOT NULL,
                carrier_type            VARCHAR NOT NULL,
                country                 VARCHAR,
                fleet_size              BIGINT,
                fleet_size_unit         VARCHAR,
                vehicle_count           BIGINT,
                reliability_score       DOUBLE,
                market_share_pct        DOUBLE,
                on_time_performance_pct DOUBLE,
                avg_delay_hours         DOUBLE,
                customer_rating         DOUBLE,
                source                  VARCHAR NOT NULL DEFAULT 'freightpulse_carriers',
                ingested_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE INDEX IF NOT EXISTS idx_carriers_snapshot ON carriers(snapshot_date);
            CREATE INDEX IF NOT EXISTS idx_carriers_code ON carriers(carrier_code);
        """,
        down_sql="DROP TABLE IF EXISTS carriers;",
    ),
    Migration(
        version="202608280001",
        description="Widen trade_flow for Eurostat Comext (ISO-alpha codes, EUR values)",
        # trade_flow.reporter_code/partner_code were INTEGER to match UN
        # Comtrade's numeric UN M49 codes. Eurostat Comext reports ISO-alpha-2
        # codes ("DE", "US"), which can't cast into an INTEGER column. The
        # table has 0 rows so far (UN_COMTRADE_API_KEY was never registered),
        # so this is a zero-data-risk widen rather than a real migration.
        # Comext values are EUR, not USD like Comtrade's trade_value_usd, so a
        # currency column disambiguates instead of silently mislabeling one as
        # the other.
        up_sql="""
            ALTER TABLE trade_flow ALTER COLUMN reporter_code TYPE VARCHAR;
            ALTER TABLE trade_flow ALTER COLUMN partner_code TYPE VARCHAR;
            ALTER TABLE trade_flow ADD COLUMN IF NOT EXISTS currency VARCHAR DEFAULT 'USD';
        """,
        down_sql="",
    ),
]


def ensure_migrations_table() -> None:
    """Create the schema_migrations tracking table if it doesn't exist."""
    conn = get_connection()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version         VARCHAR PRIMARY KEY,
                description     VARCHAR,
                applied_at      TIMESTAMP DEFAULT now()
            );
        """)
    finally:
        conn.close()


def get_applied_versions(conn: duckdb.DuckDBPyConnection) -> set[str]:
    """Get set of migration versions already applied."""
    result = conn.execute(
        "SELECT version FROM schema_migrations ORDER BY version"
    ).fetchall()
    return {row[0] for row in result}


def apply_pending_migrations(conn: duckdb.DuckDBPyConnection | None = None) -> list[str]:
    """Apply all pending migrations in order.

    Args:
        conn: Optional existing connection. If None, opens a new one.

    Returns:
        List of version strings that were applied.
    """
    own_conn = conn is None
    if conn is None:
        conn = get_connection()

    try:
        ensure_migrations_table()
        applied = get_applied_versions(conn)
        pending = [m for m in MIGRATIONS if m.version not in applied]

        if not pending:
            latest = MIGRATIONS[-1].version if MIGRATIONS else "none"
            logger.info("Schema is up to date (version %s)", latest)
            return []

        applied_versions: list[str] = []
        for migration in pending:
            logger.info(
                "Applying migration %s: %s",
                migration.version,
                migration.description,
            )
            try:
                # A statement failure must abort the migration. Swallowing it
                # and recording the version anyway makes a failed ALTER look
                # permanently successful, so it is never retried — which is how
                # ais_positions.vessel_type drifted unnoticed.
                #
                # Note: splitting on ";" is naive and would break on a semicolon
                # inside a string literal. No migration needs that yet.
                for statement in migration.up_sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        conn.execute(statement)

                conn.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                    [migration.version, migration.description],
                )
                applied_versions.append(migration.version)
                logger.info("Migration %s applied successfully", migration.version)

            except Exception as e:
                logger.error(
                    "Migration %s (%s) failed: %s. Not recording it as applied; "
                    "it will be retried on the next run. Stopping here so later "
                    "migrations do not run against a half-migrated schema.",
                    migration.version,
                    migration.description,
                    e,
                )
                break

        return applied_versions

    finally:
        if own_conn:
            conn.close()


def rollback_migration(
    version: str,
    conn: duckdb.DuckDBPyConnection | None = None,
) -> bool:
    """Rollback a specific migration.

    Args:
        version: The version string to rollback.
        conn: Optional existing connection.

    Returns:
        True if rollback succeeded.
    """
    own_conn = conn is None
    if conn is None:
        conn = get_connection()

    try:
        migration = next((m for m in MIGRATIONS if m.version == version), None)
        if migration is None:
            logger.error("Migration %s not found", version)
            return False

        if not migration.down_sql.strip():
            logger.warning("Migration %s has no rollback SQL", version)
            return False

        for statement in migration.down_sql.split(";"):
            statement = statement.strip()
            if statement:
                conn.execute(statement)

        conn.execute(
            "DELETE FROM schema_migrations WHERE version = ?", [version]
        )
        logger.info("Migration %s rolled back", version)
        return True

    except Exception as e:
        logger.error("Rollback of %s failed: %s", version, e)
        return False

    finally:
        if own_conn:
            conn.close()


def get_schema_status() -> list[dict[str, str]]:
    """Get status of all migrations."""
    ensure_migrations_table()
    conn = get_connection()
    try:
        applied = get_applied_versions(conn)
        return [
            {
                "version": m.version,
                "description": m.description,
                "status": "applied" if m.version in applied else "pending",
            }
            for m in MIGRATIONS
        ]
    finally:
        conn.close()


def get_current_version() -> str | None:
    """Get the latest applied migration version."""
    ensure_migrations_table()
    conn = get_connection()
    try:
        applied = get_applied_versions(conn)
        return max(applied) if applied else None
    finally:
        conn.close()
