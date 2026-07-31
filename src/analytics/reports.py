"""CLI entry point for the shipping pipeline."""
from __future__ import annotations

import argparse
import sys
from datetime import date

import polars as pl

from src.analytics.congestion import calculate_port_activity, estimate_congestion
from src.analytics.routes import get_active_vessels
from src.analytics.trade_flow import get_vessel_destination_summary
from src.storage.reader import list_sources, query


def print_header(title: str) -> None:
    """Print a formatted header."""
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


def print_table(df: pl.DataFrame, max_rows: int = 20) -> None:
    """Print a DataFrame as a formatted table."""
    if df.height == 0:
        print("  No data available.\n")
        return

    display_df = df.head(max_rows)
    # polars renders its own table; to_pandas() would drag in pandas, which is
    # not a declared dependency and is absent on CI.
    with pl.Config(
        tbl_formatting="ASCII_FULL_CONDENSED",
        tbl_hide_dataframe_shape=True,
        tbl_hide_column_data_types=True,
        tbl_rows=max_rows,
        tbl_cols=-1,
        fmt_str_lengths=60,
    ):
        print(display_df)

    if df.height > max_rows:
        print(f"\n  ... and {df.height - max_rows} more rows")
    print()


def cmd_overview(args: argparse.Namespace) -> None:
    """Show pipeline overview."""
    print_header("Pipeline Overview")

    sources = list_sources()
    if sources.height == 0:
        print("  No data collected yet.")
        return

    print("  Data Sources:")
    print_table(sources)

    for table in ["ais_positions", "vessels", "ports", "marine_weather", "weather"]:
        if table not in ("ais_positions", "vessels", "ports", "marine_weather", "weather"):
            continue
        result = query(f"SELECT count(*) as row_count FROM {table}")
        count = result[0, "row_count"]
        print(f"  {table}: {count:,} rows")


def cmd_vessels(args: argparse.Namespace) -> None:
    """Show active vessels."""
    print_header("Active Vessels")

    from src.storage.lineage import record_analytics_run
    _started = __import__("datetime").datetime.now()

    df = get_active_vessels(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
    )

    record_analytics_run("routes", _started, rows_output=df.height, params={
        "date_from": args.date_from, "date_to": args.date_to, "source": args.source,
    })
    print_table(df, max_rows=args.limit)


def cmd_ports(args: argparse.Namespace) -> None:
    """Show port activity."""
    print_header("Port Activity")

    from src.storage.lineage import record_analytics_run
    _started = __import__("datetime").datetime.now()

    df = calculate_port_activity(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
    )

    record_analytics_run("congestion", _started, rows_output=df.height, params={
        "date_from": args.date_from, "date_to": args.date_to, "source": args.source,
    })
    print_table(df, max_rows=args.limit)


def cmd_congestion(args: argparse.Namespace) -> None:
    """Show congestion estimates."""
    print_header("Port Congestion Estimates")

    from src.storage.lineage import record_analytics_run
    _started = __import__("datetime").datetime.now()

    df = estimate_congestion(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
        speed_threshold=args.speed_threshold,
    )

    record_analytics_run("congestion", _started, rows_output=df.height, params={
        "date_from": args.date_from, "date_to": args.date_to, "source": args.source,
        "speed_threshold": args.speed_threshold,
    })
    print_table(df, max_rows=args.limit)


def cmd_destinations(args: argparse.Namespace) -> None:
    """Show destination summary."""
    print_header("Vessel Destinations")

    from src.storage.lineage import record_analytics_run
    _started = __import__("datetime").datetime.now()

    df = get_vessel_destination_summary(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
    )

    record_analytics_run("trade_flow", _started, rows_output=df.height, params={
        "date_from": args.date_from, "date_to": args.date_to, "source": args.source,
    })
    print_table(df, max_rows=args.limit)


def cmd_routes(args: argparse.Namespace) -> None:
    """Show route pairs."""
    print_header("Port-to-Port Routes")

    from src.analytics.trade_flow import analyze_port_pairs
    from src.storage.lineage import record_analytics_run
    _started = __import__("datetime").datetime.now()

    df = analyze_port_pairs(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
    )

    record_analytics_run("trade_flow", _started, rows_output=df.height, params={
        "date_from": args.date_from, "date_to": args.date_to, "source": args.source,
    })
    print_table(df, max_rows=args.limit)


def cmd_weather(args: argparse.Namespace) -> None:
    """Show recent weather data."""
    print_header("Recent Weather Data")

    result = query(
        """
        SELECT
            latitude,
            longitude,
            avg(wave_height) as avg_wave_height,
            max(wave_height) as max_wave_height,
            avg(swell_wave_height) as avg_swell_height,
            avg(sea_surface_temperature) as avg_sst,
            count(*) as readings
        FROM marine_weather
        WHERE partition_date >= current_date - interval '7 days'
        GROUP BY latitude, longitude
        ORDER BY readings DESC
        LIMIT ?
        """,
        [args.limit],
    )

    print_table(result)


def cmd_status(args: argparse.Namespace) -> None:
    """Show pipeline status and health."""
    print_header("Pipeline Status")

    from src.monitoring.quality import get_quality_report, print_quality_report

    report = get_quality_report(stale_threshold_hours=args.threshold)
    print_quality_report(report)

    if args.warnings_only:
        from src.monitoring.quality import check_quality_thresholds

        warnings = check_quality_thresholds(report)
        if warnings:
            print("\nWARNINGS:")
            for w in warnings:
                print(f"  - {w}")
        else:
            print("\nNo warnings.")


def cmd_collect(args: argparse.Namespace) -> None:
    """Run data collection."""
    print_header("Data Collection")

    from src.monitoring.collect_all import print_collection_report, run_all_collectors

    report = run_all_collectors(
        sources=args.sources,
        force=args.force,
        notify=not args.no_notify,
    )
    print_collection_report(report)

    sys.exit(0 if report.failed == 0 else 1)


def cmd_backfill(args: argparse.Namespace) -> None:
    """Run historical backfill."""
    print_header("Historical Backfill")

    from src.monitoring.backfill import print_backfill_report, run_backfill

    results = run_backfill(
        start_date=args.start_date,
        end_date=args.end_date,
        sources=args.sources,
        notify=not args.no_notify,
    )
    print_backfill_report(results)


def cmd_quality(args: argparse.Namespace) -> None:
    """Show detailed quality metrics."""
    print_header("Data Quality Metrics")

    from src.monitoring.quality import get_quality_report, print_quality_report

    report = get_quality_report(stale_threshold_hours=args.threshold)
    print_quality_report(report)


def cmd_dashboard(args: argparse.Namespace) -> None:
    """Generate HTML dashboard."""
    from src.monitoring.dashboard import generate_dashboard

    path = generate_dashboard(output_path=args.output)
    print(f"Dashboard generated: {path}")


def cmd_schema(args: argparse.Namespace) -> None:
    """Show schema version status."""
    print_header("Schema Version Status")

    from src.storage.migrations import get_current_version, get_schema_status

    version = get_current_version()
    print(f"  Current version: {version or 'none'}\n")

    status = get_schema_status()
    for s in status:
        marker = "APPLIED" if s["status"] == "applied" else "PENDING"
        print(f"  [{marker}] {s['version']}: {s['description']}")


def cmd_migrate(args: argparse.Namespace) -> None:
    """Apply pending schema migrations."""
    print_header("Schema Migration")

    from src.storage.migrations import (
        apply_pending_migrations,
        get_current_version,
        get_schema_status,
    )

    version_before = get_current_version()
    print(f"  Current version: {version_before or 'none'}")

    applied = apply_pending_migrations()

    if applied:
        print(f"\n  Applied {len(applied)} migration(s):")
        for v in applied:
            print(f"    - {v}")

    # A migration that fails is deliberately not recorded, so it stays pending.
    # Reporting "up to date" here would repeat the misreporting this command
    # exists to surface.
    still_pending = [s for s in get_schema_status() if s["status"] == "pending"]
    if still_pending:
        print(f"\n  {len(still_pending)} migration(s) still pending — see the log:")
        for s in still_pending:
            print(f"    - {s['version']}: {s['description']}")
        raise SystemExit(1)

    if not applied:
        print("\n  Schema is already up to date.")


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="sdp",
        description="Shipping Data Pipeline CLI",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Overview
    overview = subparsers.add_parser("overview", help="Show pipeline overview")
    overview.set_defaults(func=cmd_overview)

    # Vessels
    vessels = subparsers.add_parser("vessels", help="Show active vessels")
    vessels.add_argument("--date-from", type=date.fromisoformat, default=None)
    vessels.add_argument("--date-to", type=date.fromisoformat, default=None)
    vessels.add_argument("--source", default=None)
    vessels.add_argument("--limit", type=int, default=20)
    vessels.set_defaults(func=cmd_vessels)

    # Ports
    ports = subparsers.add_parser("ports", help="Show port activity")
    ports.add_argument("--date-from", type=date.fromisoformat, default=None)
    ports.add_argument("--date-to", type=date.fromisoformat, default=None)
    ports.add_argument("--source", default=None)
    ports.add_argument("--limit", type=int, default=20)
    ports.set_defaults(func=cmd_ports)

    # Congestion
    congestion = subparsers.add_parser("congestion", help="Show congestion estimates")
    congestion.add_argument("--date-from", type=date.fromisoformat, default=None)
    congestion.add_argument("--date-to", type=date.fromisoformat, default=None)
    congestion.add_argument("--source", default=None)
    congestion.add_argument("--speed-threshold", type=float, default=1.0)
    congestion.add_argument("--limit", type=int, default=20)
    congestion.set_defaults(func=cmd_congestion)

    # Destinations
    destinations = subparsers.add_parser("destinations", help="Show vessel destinations")
    destinations.add_argument("--date-from", type=date.fromisoformat, default=None)
    destinations.add_argument("--date-to", type=date.fromisoformat, default=None)
    destinations.add_argument("--source", default=None)
    destinations.add_argument("--limit", type=int, default=20)
    destinations.set_defaults(func=cmd_destinations)

    # Routes
    routes = subparsers.add_parser("routes", help="Show port-to-port routes")
    routes.add_argument("--date-from", type=date.fromisoformat, default=None)
    routes.add_argument("--date-to", type=date.fromisoformat, default=None)
    routes.add_argument("--source", default=None)
    routes.add_argument("--limit", type=int, default=20)
    routes.set_defaults(func=cmd_routes)

    # Weather
    weather = subparsers.add_parser("weather", help="Show recent weather data")
    weather.add_argument("--limit", type=int, default=20)
    weather.set_defaults(func=cmd_weather)

    # Status (new)
    status = subparsers.add_parser("status", help="Show pipeline health status")
    status.add_argument("--threshold", type=float, default=168.0,
                       help="Staleness threshold in hours (default: 168)")
    status.add_argument("--warnings-only", action="store_true",
                       help="Only show warnings")
    status.set_defaults(func=cmd_status)

    # Collect (new)
    collect = subparsers.add_parser("collect", help="Run data collection")
    collect.add_argument("--sources", nargs="*",
                        help="Specific sources to collect (default: all)")
    collect.add_argument("--force", action="store_true",
                        help="Force collection even if recently collected")
    collect.add_argument("--no-notify", action="store_true",
                        help="Disable notifications")
    collect.set_defaults(func=cmd_collect)

    backfill = subparsers.add_parser("backfill", help="Run historical data backfill")
    backfill.add_argument(
        "--start-date", type=date.fromisoformat, required=True,
        help="Start date (YYYY-MM-DD)",
    )
    backfill.add_argument(
        "--end-date", type=date.fromisoformat, required=True,
        help="End date (YYYY-MM-DD, inclusive)",
    )
    backfill.add_argument("--sources", nargs="*",
                        help="Specific sources to backfill (default: all date-aware sources)")
    backfill.add_argument("--no-notify", action="store_true",
                        help="Disable notifications")
    backfill.set_defaults(func=cmd_backfill)

    # Quality (new)
    quality = subparsers.add_parser("quality", help="Show detailed quality metrics")
    quality.add_argument("--threshold", type=float, default=168.0,
                        help="Staleness threshold in hours (default: 168)")
    quality.set_defaults(func=cmd_quality)

    # Dashboard
    dashboard = subparsers.add_parser("dashboard", help="Generate HTML dashboard")
    dashboard.add_argument("--output", type=str, default=None,
                          help="Output path (default: storage/dashboard.html)")
    dashboard.set_defaults(func=cmd_dashboard)

    # Schema
    schema = subparsers.add_parser("schema", help="Show schema version status")
    schema.set_defaults(func=cmd_schema)

    # Migrate
    migrate = subparsers.add_parser("migrate", help="Apply pending schema migrations")
    migrate.set_defaults(func=cmd_migrate)

    return parser


def main() -> None:
    """Main entry point for the CLI."""
    parser = build_parser()
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    args.func(args)


if __name__ == "__main__":
    main()
