"""Weekly port panel: ship calls per port per week, the base for forecasting.

One row per port per week (Monday to Sunday), summed from IMF PortWatch's daily
``port_activity``. Daily counts at most ports are mostly 0s and 1s (half the
ports average under one call a day), so weekly totals are what is worth
forecasting.

PortWatch lists every port every day, zeros included, so a week with fewer
than 7 days reported is either the current, unfinished week or a gap in the
feed. Its totals are partial: ``days_reported`` says how many days they cover,
and ``is_complete_week`` is false. Use complete weeks only when comparing or
training; a partial week read as a quiet one would teach a model a slump that
never happened.
"""
from __future__ import annotations

import logging

import duckdb

logger = logging.getLogger(__name__)

TABLE = "port_weekly"

_SUMMED = [
    "portcalls",
    "portcalls_container",
    "portcalls_dry_bulk",
    "portcalls_general_cargo",
    "portcalls_roro",
    "portcalls_tanker",
    "import_total",
    "export_total",
]


def create_port_weekly(conn: duckdb.DuckDBPyConnection) -> int:
    """Rebuild ``port_weekly``. Returns its row count."""
    sums = ",\n            ".join(f"sum({c}) AS {c}" for c in _SUMMED)
    conn.execute(f"""
        CREATE OR REPLACE TABLE {TABLE} AS
        SELECT
            CAST(date_trunc('week', activity_date) AS DATE) AS week_start,
            port_id,
            -- A port's name can be corrected upstream; take the latest.
            arg_max(port_name, activity_date) AS port_name,
            arg_max(country, activity_date) AS country,
            arg_max(iso3, activity_date) AS iso3,
            count(DISTINCT activity_date) AS days_reported,
            count(DISTINCT activity_date) = 7 AS is_complete_week,
            {sums}
        FROM port_activity
        WHERE activity_date IS NOT NULL AND port_id IS NOT NULL
        GROUP BY ALL
        ORDER BY port_id, week_start
    """)
    row = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
    count = int(row[0]) if row else 0
    logger.info("Created %s with %d rows", TABLE, count)
    return count
