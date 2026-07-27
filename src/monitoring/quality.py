"""Data quality monitoring checks."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import duckdb

from src.storage.schema import ALL_TABLES
from src.storage.writer import get_db_path

logger = logging.getLogger(__name__)


@dataclass
class StaleSource:
    """A source that hasn't been updated recently."""

    source: str
    last_partition: datetime
    hours_stale: float


@dataclass
class TableQuality:
    """Quality metrics for a single table."""

    table_name: str
    row_count: int
    null_rates: dict[str, float]
    stale_sources: list[StaleSource]
    partition_count: int
    last_update: datetime | None


@dataclass
class QualityReport:
    """Overall quality report for the pipeline."""

    tables: list[TableQuality]
    total_rows: int
    sources_count: int
    stale_count: int
    generated_at: datetime

    @property
    def is_healthy(self) -> bool:
        """Check if pipeline is healthy (no stale sources)."""
        return self.stale_count == 0


def get_table_quality(
    table_name: str,
    stale_threshold_hours: float = 48.0,
) -> TableQuality | None:
    """Get quality metrics for a single table.

    Args:
        table_name: Name of the table to check.
        stale_threshold_hours: Hours after which a source is considered stale.

    Returns:
        TableQuality with metrics, or None if table doesn't exist.
    """
    db_path = get_db_path()
    if not db_path.exists():
        return None

    conn = duckdb.connect(str(db_path))
    conn.execute("SET autoinstall_known_extensions=1;")
    conn.execute("SET autoload_known_extensions=1;")

    try:
        # Check if table exists
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_name = ?",
            [table_name],
        ).fetchall()

        if not tables:
            return None

        # Get row count
        row_count_result = conn.execute(
            f"SELECT count(*) FROM {table_name}"
        ).fetchone()
        assert row_count_result is not None
        row_count: int = row_count_result[0]

        # Get null rates for non-system columns
        null_rates: dict[str, float] = {}
        columns = conn.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
            [table_name],
        ).fetchall()

        for col_name, in columns:
            if col_name in ("source", "partition_date", "ingested_at"):
                continue
            try:
                result = conn.execute(
                    f"SELECT count(*) as total, "
                    f"count(CASE WHEN {col_name} IS NULL THEN 1 END) as nulls "
                    f"FROM {table_name}"
                ).fetchone()
                if result and result[0] > 0:
                    null_rate = result[1] / result[0]
                    if null_rate > 0:
                        null_rates[col_name] = round(null_rate, 4)
            except Exception:
                pass

        # Get stale sources
        stale_sources: list[StaleSource] = []
        partition_count = 0
        last_update: datetime | None = None

        if "partition_date" in [c[0] for c in columns]:
            try:
                source_stats = conn.execute(
                    f"""
                    SELECT source,
                           max(partition_date) as latest,
                           count(DISTINCT partition_date) as partitions
                    FROM {table_name}
                    GROUP BY source
                    """
                ).fetchall()

                now = datetime.now()
                for source_name, latest, partitions in source_stats:
                    if source_name is None:
                        continue
                    partition_count += partitions
                    if last_update is None or (latest and latest > last_update):
                        last_update = latest

                    if latest:
                        hours_stale = (now - latest).total_seconds() / 3600
                        if hours_stale > stale_threshold_hours:
                            stale_sources.append(
                                StaleSource(
                                    source=source_name,
                                    last_partition=latest,
                                    hours_stale=round(hours_stale, 1),
                                )
                            )
            except Exception:
                pass

        return TableQuality(
            table_name=table_name,
            row_count=row_count,
            null_rates=null_rates,
            stale_sources=stale_sources,
            partition_count=partition_count,
            last_update=last_update,
        )

    finally:
        conn.close()


def get_quality_report(
    stale_threshold_hours: float = 48.0,
) -> QualityReport:
    """Generate a quality report for all tables.

    Args:
        stale_threshold_hours: Hours after which a source is considered stale.

    Returns:
        QualityReport with metrics for all tables.
    """
    tables: list[TableQuality] = []
    total_rows = 0
    all_stale: list[StaleSource] = []

    for table_schema in ALL_TABLES:
        quality = get_table_quality(table_schema.name, stale_threshold_hours)
        if quality is not None:
            tables.append(quality)
            total_rows += quality.row_count
            all_stale.extend(quality.stale_sources)

    return QualityReport(
        tables=tables,
        total_rows=total_rows,
        sources_count=sum(t.partition_count for t in tables),
        stale_count=len(all_stale),
        generated_at=datetime.now(),
    )


def print_quality_report(report: QualityReport) -> None:
    """Print a formatted quality report."""
    print("\n" + "=" * 60)
    print("DATA QUALITY REPORT")
    print("=" * 60)
    print(f"Generated: {report.generated_at:%Y-%m-%d %H:%M:%S}")
    print(f"Total rows: {report.total_rows:,}")
    print(f"Active sources: {report.sources_count}")
    print(f"Stale sources: {report.stale_count}")

    if report.stale_count > 0:
        print("\n--- Stale Sources ---")
        for table in report.tables:
            for stale in table.stale_sources:
                print(f"  {stale.source} (in {table.table_name}): "
                      f"{stale.hours_stale:.1f}h stale")

    print("\n--- Table Details ---")
    for table in report.tables:
        status = "OK" if not table.stale_sources else "STALE"
        print(f"\n  {table.table_name} [{status}]")
        print(f"    Rows: {table.row_count:,}")
        print(f"    Partitions: {table.partition_count}")

        if table.null_rates:
            print("    Null rates:")
            for col, rate in sorted(table.null_rates.items()):
                print(f"      {col}: {rate:.1%}")

        if table.last_update:
            print(f"    Last update: {table.last_update}")

    print("\n" + "=" * 60)


def check_quality_thresholds(
    report: QualityReport,
    max_null_rate: float = 0.5,
    max_stale_hours: float = 168.0,  # 7 days
) -> list[str]:
    """Check if quality metrics exceed thresholds.

    Args:
        report: Quality report to check.
        max_null_rate: Maximum acceptable null rate.
        max_stale_hours: Maximum acceptable staleness in hours.

    Returns:
        List of warning messages.
    """
    warnings: list[str] = []

    for table in report.tables:
        for col, rate in table.null_rates.items():
            if rate > max_null_rate:
                warnings.append(
                    f"{table.table_name}.{col}: null rate {rate:.1%} > {max_null_rate:.1%}"
                )

        for stale in table.stale_sources:
            if stale.hours_stale > max_stale_hours:
                warnings.append(
                    f"{stale.source}: stale for {stale.hours_stale:.0f}h "
                    f"(threshold: {max_stale_hours:.0f}h)"
                )

    return warnings


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Data quality monitoring")
    parser.add_argument(
        "--check-staleness",
        action="store_true",
        help="Fail if any source is stale",
    )
    parser.add_argument(
        "--check-nulls",
        action="store_true",
        help="Fail if null rates exceed threshold",
    )
    parser.add_argument(
        "--threshold-hours",
        type=float,
        default=168.0,
        help="Staleness threshold in hours (default: 168 = 7 days)",
    )
    args = parser.parse_args()

    report = get_quality_report(stale_threshold_hours=args.threshold_hours)
    print_quality_report(report)

    if args.check_staleness or args.check_nulls:
        warnings = check_quality_thresholds(report)
        if warnings:
            print("\nQUALITY WARNINGS:")
            for w in warnings:
                print(f"  - {w}")
            exit(1)
        print("\nAll quality checks passed.")
