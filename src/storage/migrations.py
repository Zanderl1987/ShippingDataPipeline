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
                for statement in migration.up_sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        try:
                            conn.execute(statement)
                        except Exception as stmt_err:
                            logger.debug("Statement skipped: %s", stmt_err)

                conn.execute(
                    "INSERT INTO schema_migrations (version, description) VALUES (?, ?)",
                    [migration.version, migration.description],
                )
                applied_versions.append(migration.version)
                logger.info("Migration %s applied successfully", migration.version)

            except Exception as e:
                logger.error(
                    "Migration %s failed: %s. Stopping.",
                    migration.version,
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
