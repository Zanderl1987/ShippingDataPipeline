"""Alert on what changed in a collection run, not on how the data looks overall.

The older check (quality.check_quality_thresholds) flags every column over 50%
null. Most such columns are sparse by design (AIS sources that never send a
field, sanctions entries that aren't vessels), so it warned about the same ~50
columns every day and a real break would have been lost among them. These
checks compare the run with what was there before it:

- **Row drop**: a table ends the run with noticeably fewer rows than it started
  with. Tables are cumulative (seeded from HuggingFace, then added to), so this
  means rows were lost, not just not collected.
- **Null spike**: for one source, the rows written this run are much emptier in
  some column than that source's older rows (a feed renamed or dropped a field).
- **Stale source**: a source already in a table wrote nothing to it recently.
  Every collector rewrites what it fetches each run, so ``ingested_at`` is when
  a source last delivered.

Usage in a run::

    baseline = take_baseline(conn)
    ...  # collect, curate
    alerts = check_run(conn, baseline)
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

import duckdb

from src.storage.schema import ALL_TABLES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AlertThresholds:
    """When a change is worth an alert."""

    #: Fraction of a table's rows a run may remove. Cleaning and dedup trim a
    #: few (26 of ~250K AIS rows on the first cleaning run).
    max_row_drop: float = 0.01
    #: Rise in a column's null rate, new rows vs the same source's older rows,
    #: as a fraction (0.25 = 25 percentage points).
    null_rate_rise: float = 0.25
    #: Rows needed on each side before null rates are compared.
    min_rows: int = 50
    #: Hours since a source last wrote to a table before it counts as stale.
    #: Every collector runs daily in CI, so two missed runs.
    stale_hours: float = 48.0
    #: Per-source overrides of ``stale_hours``.
    stale_hours_by_source: dict[str, float] = field(default_factory=dict)


DEFAULT_THRESHOLDS = AlertThresholds(
    stale_hours_by_source={
        # Loads only new monthly files, which NOAA publishes months late and
        # irregularly; 100 days is roughly three missed months.
        "noaa_marinecadastre": 100 * 24.0,
    },
)

#: Bookkeeping tables, not data.
_INTERNAL = {"source_tracking", "lineage_events", "schema_migrations", "schema_versions"}
_SYSTEM_COLUMNS = {"source", "partition_date", "ingested_at"}


@dataclass
class Alert:
    kind: str  # row_drop, null_spike, stale_source
    table: str
    message: str
    source: str | None = None


def _monitored_tables(conn: duckdb.DuckDBPyConnection) -> list[str]:
    """Schema tables that exist and aren't internal."""
    existing = {
        r[0] for r in conn.execute("SELECT table_name FROM information_schema.tables").fetchall()
    }
    return [t.name for t in ALL_TABLES if t.name in existing and t.name not in _INTERNAL]


def _columns(conn: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = ? ORDER BY ordinal_position",
            [table],
        ).fetchall()
    ]


def db_now(conn: duckdb.DuckDBPyConnection) -> datetime:
    """The database's clock, in the same zone ``ingested_at DEFAULT now()`` uses."""
    row = conn.execute("SELECT now()::TIMESTAMP").fetchone()
    assert row is not None
    value: datetime = row[0]
    return value


@dataclass
class Baseline:
    """Table state at the start of a run."""

    started_at: datetime
    row_counts: dict[str, int]


def take_baseline(conn: duckdb.DuckDBPyConnection) -> Baseline:
    """Record row counts and the run's start time, before anything is collected."""
    counts: dict[str, int] = {}
    for table in _monitored_tables(conn):
        row = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()
        counts[table] = int(row[0]) if row else 0
    return Baseline(started_at=db_now(conn), row_counts=counts)


def check_row_drops(
    conn: duckdb.DuckDBPyConnection,
    baseline: Baseline,
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> list[Alert]:
    alerts: list[Alert] = []
    for table, before in baseline.row_counts.items():
        if before == 0:
            continue
        row = conn.execute(f'SELECT count(*) FROM "{table}"').fetchone()
        after = int(row[0]) if row else 0
        if (before - after) / before > thresholds.max_row_drop:
            alerts.append(
                Alert(
                    "row_drop",
                    table,
                    f"{table}: {before:,} rows at start, {after:,} at end "
                    f"({(before - after) / before:.1%} lost)",
                )
            )
    return alerts


def check_null_spikes(
    conn: duckdb.DuckDBPyConnection,
    since: datetime,
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> list[Alert]:
    """Compare each source's rows written since *since* with its older rows."""
    alerts: list[Alert] = []
    for table in _monitored_tables(conn):
        cols = _columns(conn, table)
        if "ingested_at" not in cols or "source" not in cols:
            continue
        data_cols = [c for c in cols if c not in _SYSTEM_COLUMNS]
        if not data_cols:
            continue
        counts = ", ".join(f'count("{c}")' for c in data_cols)
        rows = conn.execute(
            f'SELECT source, ingested_at >= ? AS is_new, count(*), {counts} '
            f'FROM "{table}" GROUP BY 1, 2',
            [since],
        ).fetchall()
        by_source: dict[str, dict[bool, tuple[int, ...]]] = {}
        for r in rows:
            by_source.setdefault(r[0], {})[bool(r[1])] = r[2:]
        for source, sides in sorted(by_source.items(), key=lambda kv: str(kv[0])):
            new, old = sides.get(True), sides.get(False)
            if not new or not old:
                continue
            if new[0] < thresholds.min_rows or old[0] < thresholds.min_rows:
                continue
            for i, col in enumerate(data_cols, start=1):
                new_null = 1 - new[i] / new[0]
                old_null = 1 - old[i] / old[0]
                if new_null - old_null >= thresholds.null_rate_rise:
                    alerts.append(
                        Alert(
                            "null_spike",
                            table,
                            f"{table}.{col} from {source}: {new_null:.0%} null in "
                            f"this run's {new[0]:,} rows, {old_null:.0%} before",
                            source=source,
                        )
                    )
    return alerts


def check_stale_sources(
    conn: duckdb.DuckDBPyConnection,
    now: datetime | None = None,
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> list[Alert]:
    now = now or db_now(conn)
    alerts: list[Alert] = []
    for table in _monitored_tables(conn):
        cols = _columns(conn, table)
        if "ingested_at" not in cols or "source" not in cols:
            continue
        rows = conn.execute(
            f'SELECT source, max(ingested_at) FROM "{table}" '
            "WHERE source IS NOT NULL GROUP BY 1 ORDER BY 1"
        ).fetchall()
        for source, last in rows:
            if last is None:
                continue
            limit = thresholds.stale_hours_by_source.get(source, thresholds.stale_hours)
            hours = (now - last).total_seconds() / 3600
            if hours > limit:
                alerts.append(
                    Alert(
                        "stale_source",
                        table,
                        f"{source} last wrote to {table} {hours:.0f}h ago "
                        f"(limit {limit:.0f}h)",
                        source=source,
                    )
                )
    return alerts


def check_run(
    conn: duckdb.DuckDBPyConnection,
    baseline: Baseline,
    thresholds: AlertThresholds = DEFAULT_THRESHOLDS,
) -> list[Alert]:
    """Every check, for the run that started at ``baseline``."""
    return (
        check_row_drops(conn, baseline, thresholds)
        + check_null_spikes(conn, baseline.started_at, thresholds)
        + check_stale_sources(conn, thresholds=thresholds)
    )


def write_github_annotations(alerts: list[Alert]) -> None:
    """Show alerts on the Actions run page (a no-op outside GitHub Actions).

    Warnings don't fail the job or send email; a webhook secret
    (SLACK_WEBHOOK_URL / DISCORD_WEBHOOK_URL) is what pushes them to a person.
    """
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    for a in alerts:
        print(f"::warning title=Data alert ({a.kind})::{a.message}", flush=True)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary and alerts:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(f"### Data alerts ({len(alerts)})\n\n")
            f.writelines(f"- {a.message}\n" for a in alerts)
