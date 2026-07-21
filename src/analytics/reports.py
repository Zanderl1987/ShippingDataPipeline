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
    print(display_df.to_pandas().to_string(index=False))

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
        result = query(f"SELECT count(*) as row_count FROM {table}")
        count = result[0, "row_count"]
        print(f"  {table}: {count:,} rows")


def cmd_vessels(args: argparse.Namespace) -> None:
    """Show active vessels."""
    print_header("Active Vessels")

    df = get_active_vessels(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
    )

    print_table(df, max_rows=args.limit)


def cmd_ports(args: argparse.Namespace) -> None:
    """Show port activity."""
    print_header("Port Activity")

    df = calculate_port_activity(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
    )

    print_table(df, max_rows=args.limit)


def cmd_congestion(args: argparse.Namespace) -> None:
    """Show congestion estimates."""
    print_header("Port Congestion Estimates")

    df = estimate_congestion(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
        speed_threshold=args.speed_threshold,
    )

    print_table(df, max_rows=args.limit)


def cmd_destinations(args: argparse.Namespace) -> None:
    """Show destination summary."""
    print_header("Vessel Destinations")

    df = get_vessel_destination_summary(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
    )

    print_table(df, max_rows=args.limit)


def cmd_routes(args: argparse.Namespace) -> None:
    """Show route pairs."""
    print_header("Port-to-Port Routes")

    from src.analytics.trade_flow import analyze_port_pairs

    df = analyze_port_pairs(
        date_from=args.date_from,
        date_to=args.date_to,
        source=args.source,
    )

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
            avg(wind_speed_10m) as avg_wind_speed,
            max(wind_speed_10m) as max_wind_speed,
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


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(
        prog="sdp",
        description="Shipping Data Pipeline CLI",
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    overview = subparsers.add_parser("overview", help="Show pipeline overview")
    overview.set_defaults(func=cmd_overview)

    vessels = subparsers.add_parser("vessels", help="Show active vessels")
    vessels.add_argument("--date-from", type=date.fromisoformat, default=None)
    vessels.add_argument("--date-to", type=date.fromisoformat, default=None)
    vessels.add_argument("--source", default=None)
    vessels.add_argument("--limit", type=int, default=20)
    vessels.set_defaults(func=cmd_vessels)

    ports = subparsers.add_parser("ports", help="Show port activity")
    ports.add_argument("--date-from", type=date.fromisoformat, default=None)
    ports.add_argument("--date-to", type=date.fromisoformat, default=None)
    ports.add_argument("--source", default=None)
    ports.add_argument("--limit", type=int, default=20)
    ports.set_defaults(func=cmd_ports)

    congestion = subparsers.add_parser("congestion", help="Show congestion estimates")
    congestion.add_argument("--date-from", type=date.fromisoformat, default=None)
    congestion.add_argument("--date-to", type=date.fromisoformat, default=None)
    congestion.add_argument("--source", default=None)
    congestion.add_argument("--speed-threshold", type=float, default=1.0)
    congestion.add_argument("--limit", type=int, default=20)
    congestion.set_defaults(func=cmd_congestion)

    destinations = subparsers.add_parser("destinations", help="Show vessel destinations")
    destinations.add_argument("--date-from", type=date.fromisoformat, default=None)
    destinations.add_argument("--date-to", type=date.fromisoformat, default=None)
    destinations.add_argument("--source", default=None)
    destinations.add_argument("--limit", type=int, default=20)
    destinations.set_defaults(func=cmd_destinations)

    routes = subparsers.add_parser("routes", help="Show port-to-port routes")
    routes.add_argument("--date-from", type=date.fromisoformat, default=None)
    routes.add_argument("--date-to", type=date.fromisoformat, default=None)
    routes.add_argument("--source", default=None)
    routes.add_argument("--limit", type=int, default=20)
    routes.set_defaults(func=cmd_routes)

    weather = subparsers.add_parser("weather", help="Show recent weather data")
    weather.add_argument("--limit", type=int, default=20)
    weather.set_defaults(func=cmd_weather)

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
