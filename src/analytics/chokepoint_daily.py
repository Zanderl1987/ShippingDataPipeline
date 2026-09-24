"""Daily chokepoint panel: is traffic through a chokepoint unusual right now?

One row per chokepoint per day, built from IMF PortWatch (``chokepoint_transits``,
``chokepoint_profiles``) and PortWatch's GDACS hazard feeds (``disruption_events``).

Daily counts are noisy (a small strait sees 2 ships one day and 5 the next), so
comparisons use 7-day averages, each against:

- the same 7 days a year earlier (364 days back, so weekdays line up), which
  removes seasonality; and
- the 28 days before the 7-day window, which shows a recent break.

An average is NULL until its window is complete. Hazard events are matched when
the day falls within the event's dates and the event's reported point is within
``EVENT_RADIUS_KM`` of the chokepoint. A cyclone is reported as one point, so a
storm tracking past a strait can be missed; treat the match as a hint.
"""
from __future__ import annotations

import logging

import duckdb

logger = logging.getLogger(__name__)

TABLE = "chokepoint_daily"
EVENT_RADIUS_KM = 500


def _pct(now: str, then: str) -> str:
    return f"round(100 * ({now} / nullif({then}, 0) - 1), 1)"


def create_chokepoint_daily(conn: duckdb.DuckDBPyConnection) -> int:
    """Rebuild ``chokepoint_daily``. Returns its row count."""
    conn.execute(f"""
        CREATE OR REPLACE TABLE {TABLE} AS
        WITH t AS (
            SELECT transit_date, chokepoint_id, chokepoint_name,
                   n_total, n_tanker, n_container, n_dry_bulk, capacity
            FROM chokepoint_transits
        ),
        w AS (
            SELECT *,
                CASE WHEN count(*) OVER w7 = 7 THEN avg(n_total) OVER w7 END AS n_total_7d,
                CASE WHEN count(*) OVER w7 = 7 THEN avg(n_tanker) OVER w7 END AS n_tanker_7d,
                CASE WHEN count(*) OVER w7 = 7 THEN avg(capacity) OVER w7 END AS capacity_7d,
                CASE WHEN count(*) OVER prior = 28 THEN avg(n_total) OVER prior END
                    AS n_total_prior_28d
            FROM t
            WINDOW
                w7 AS (PARTITION BY chokepoint_id ORDER BY transit_date
                       RANGE BETWEEN INTERVAL 6 DAY PRECEDING AND CURRENT ROW),
                prior AS (PARTITION BY chokepoint_id ORDER BY transit_date
                          RANGE BETWEEN INTERVAL 34 DAY PRECEDING AND INTERVAL 7 DAY PRECEDING)
        ),
        -- The two PortWatch feeds list many of the same GDACS events.
        ev AS (
            SELECT DISTINCT
                event_type, event_name, upper(alert_level) AS alert_level,
                latitude, longitude,
                CAST(from_date AS DATE) AS d0,
                CAST(CASE WHEN to_date IS NULL AND is_current THEN current_date
                          ELSE coalesce(to_date, from_date) END AS DATE) AS d1
            FROM disruption_events
            WHERE latitude IS NOT NULL AND longitude IS NOT NULL AND from_date IS NOT NULL
        ),
        near AS (
            SELECT w.chokepoint_id, w.transit_date,
                count(*) AS nearby_events,
                string_agg(DISTINCT ev.event_type || ' ' || ev.event_name, '; '
                           ORDER BY ev.event_type || ' ' || ev.event_name)
                    AS nearby_event_names,
                -- GREEN < ORANGE < RED, alphabetically too.
                max(ev.alert_level) AS nearby_max_alert
            FROM w
            JOIN chokepoint_profiles cp USING (chokepoint_id)
            JOIN ev ON w.transit_date BETWEEN ev.d0 AND ev.d1
            WHERE 2 * 6371 * asin(sqrt(
                    pow(sin(radians(ev.latitude - cp.latitude) / 2), 2)
                    + cos(radians(cp.latitude)) * cos(radians(ev.latitude))
                      * pow(sin(radians(ev.longitude - cp.longitude) / 2), 2)
                  )) <= {EVENT_RADIUS_KM}
            GROUP BY 1, 2
        )
        SELECT
            w.transit_date,
            w.chokepoint_id,
            w.chokepoint_name,
            cp.latitude,
            cp.longitude,
            w.n_total, w.n_tanker, w.n_container, w.n_dry_bulk, w.capacity,
            round(w.n_total_7d, 2) AS n_total_7d,
            round(w.n_tanker_7d, 2) AS n_tanker_7d,
            round(w.capacity_7d, 0) AS capacity_7d,
            round(y.n_total_7d, 2) AS n_total_7d_year_ago,
            round(y.capacity_7d, 0) AS capacity_7d_year_ago,
            {_pct("w.n_total_7d", "y.n_total_7d")} AS n_total_vs_year_ago_pct,
            {_pct("w.n_tanker_7d", "y.n_tanker_7d")} AS n_tanker_vs_year_ago_pct,
            {_pct("w.capacity_7d", "y.capacity_7d")} AS capacity_vs_year_ago_pct,
            {_pct("w.n_total_7d", "w.n_total_prior_28d")} AS n_total_vs_prior_28d_pct,
            coalesce(near.nearby_events, 0) AS nearby_events,
            near.nearby_event_names,
            near.nearby_max_alert
        FROM w
        LEFT JOIN w AS y
            ON y.chokepoint_id = w.chokepoint_id
            AND y.transit_date = w.transit_date - INTERVAL 364 DAY
        LEFT JOIN chokepoint_profiles cp ON cp.chokepoint_id = w.chokepoint_id
        LEFT JOIN near
            ON near.chokepoint_id = w.chokepoint_id AND near.transit_date = w.transit_date
        ORDER BY w.chokepoint_id, w.transit_date
    """)
    row = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()
    count = int(row[0]) if row else 0
    logger.info("Created %s with %d rows", TABLE, count)
    return count
