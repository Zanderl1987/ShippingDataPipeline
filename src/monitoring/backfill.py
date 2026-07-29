"""Unified backfill engine for historical data collection."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Callable

from src.monitoring.collect_all import CollectorDef
from src.monitoring.notify import Notifier, notify_collection_error
from src.storage.tracker import SourceTracker

logger = logging.getLogger(__name__)


@dataclass
class BackfillResult:
    """Result of a backfill operation."""
    source: str
    start_date: date
    end_date: date
    days_attempted: int = 0
    days_succeeded: int = 0
    total_rows: int = 0
    errors: list[str] = field(default_factory=list)


def _get_backfill_adapters() -> dict[str, Callable[..., list[Callable[[], int]]]]:
    """Return source-specific adapters that convert date ranges into callable lists.

    Each adapter takes (start_date, end_date) and returns a list of callables,
    one per unit (day or month) in the range. Each callable fetches data for
    that unit and returns the row count.
    """
    adapters: dict[str, Callable[..., list[Callable[[], int]]]] = {}

    def _gfw_adapter(start: date, end: date) -> list[Callable[[], int]]:
        from src.collectors.global_fishing_watch import collect_events
        fns = []
        current = start
        while current <= end:
            next_day = current + timedelta(days=1)
            s, e = current.isoformat(), next_day.isoformat()
            fns.append(lambda s=s, e=e: collect_events(start_date=s, end_date=e))
            current = next_day
        return fns
    adapters["global_fishing_watch"] = _gfw_adapter

    def _imf_adapter(start: date, end: date) -> list[Callable[[], int]]:
        from src.collectors.imf_portwatch import collect_chokepoint_transits
        fns = []
        current = start
        while current <= end:
            next_day = current + timedelta(days=1)
            s, e = current.isoformat(), next_day.isoformat()
            fns.append(lambda s=s, e=e: collect_chokepoint_transits(start_date=s, end_date=e))
            current = next_day
        return fns
    adapters["imf_portwatch"] = _imf_adapter

    def _vesselapi_adapter(start: date, end: date) -> list[Callable[[], int]]:
        from src.collectors.vesselapi import collect_port_events
        fns = []
        current = start
        while current <= end:
            next_day = current + timedelta(days=1)
            s, e = current.isoformat(), next_day.isoformat()
            fns.append(lambda s=s, e=e: collect_port_events(time_from=s, time_to=e, limit=50))
            current = next_day
        return fns
    adapters["vesselapi"] = _vesselapi_adapter

    def _open_meteo_adapter(start: date, end: date) -> list[Callable[[], int]]:
        from src.collectors.open_meteo import collect_marine
        total_days = (end - start).days + 1
        return [lambda td=total_days: collect_marine(latitude=1.264, longitude=103.82, past_days=td)]
    adapters["open_meteo"] = _open_meteo_adapter

    def _comtrade_adapter(start: date, end: date) -> list[Callable[[], int]]:
        from src.collectors.un_comtrade import collect_trade_data
        fns = []
        current = start.replace(day=1)
        while current <= end:
            period = current.strftime("%Y%m")
            fns.append(lambda p=period: collect_trade_data(reporter_code=156, period=p))
            if current.month == 12:
                current = current.replace(year=current.year + 1, month=1)
            else:
                current = current.replace(month=current.month + 1)
        return fns
    adapters["un_comtrade"] = _comtrade_adapter

    def _default_adapter(start: date, end: date) -> list[Callable[[], int]]:
        return []
    adapters["_default"] = _default_adapter

    return adapters


def run_backfill(
    start_date: date,
    end_date: date,
    sources: list[str] | None = None,
    force: bool = False,
    notify: bool = True,
) -> list[BackfillResult]:
    """Run backfill for date-accepting collectors over a date range.

    For collectors that accept date parameters, iterates day-by-day (or month-by-month
    for UN Comtrade). For date-agnostic collectors, runs them once with force=True.

    Args:
        start_date: First date to backfill (inclusive).
        end_date: Last date to backfill (inclusive).
        sources: Filter to specific source names. None = all backfillable sources.
        force: Ignored (backfill always runs).
        notify: If True, send notifications on failure.

    Returns:
        List of BackfillResult per source.
    """
    from src.config import settings
    settings.ensure_dirs()

    adapters = _get_backfill_adapters()
    tracker = SourceTracker()
    notifier = Notifier.from_env() if notify else Notifier()

    collectors_map = {c.name: c for c in _import_all_collectors()}

    backfill_sources = sources or [name for name in collectors_map if name in adapters]

    results: list[BackfillResult] = []

    for source_name in backfill_sources:
        if source_name not in adapters and source_name not in collectors_map:
            logger.warning("Unknown source: %s", source_name)
            continue

        result = BackfillResult(
            source=source_name,
            start_date=start_date,
            end_date=end_date,
        )

        adapter = adapters.get(source_name)
        if adapter:
            units = adapter(start_date, end_date)
            result.days_attempted = len(units)
            for i, unit_fn in enumerate(units):
                try:
                    rows = unit_fn()
                    result.days_succeeded += 1
                    result.total_rows += rows
                    logger.info(
                        "[%s] Unit %d/%d: %d rows",
                        source_name, i + 1, len(units), rows,
                    )
                except Exception as e:
                    error_msg = f"Unit {i + 1}: {e}"
                    result.errors.append(error_msg)
                    logger.error("[%s] %s", source_name, error_msg)
                    notifier.send(notify_collection_error(source_name, e))
        else:
            collector = collectors_map.get(source_name)
            if collector:
                result.days_attempted = 1
                try:
                    collector.collect_fn()
                    last = tracker.get_last_collection(source_name)
                    rows = last["rows_written"] if last else 0
                    result.days_succeeded = 1
                    result.total_rows = rows
                except Exception as e:
                    error_msg = str(e)
                    result.errors.append(error_msg)
                    logger.error("[%s] %s", source_name, error_msg)
                    notifier.send(notify_collection_error(source_name, e))

        results.append(result)

    return results


def _import_all_collectors() -> list[CollectorDef]:
    """Import all collectors without staleness checks."""
    from src.monitoring.collect_all import get_collectors
    return get_collectors()


def print_backfill_report(results: list[BackfillResult]) -> None:
    """Print a formatted backfill report."""
    print("\n" + "=" * 60)
    print("BACKFILL REPORT")
    print("=" * 60)

    if not results:
        print("  No sources to backfill.")
        return

    total_rows = 0
    total_errors = 0

    for r in results:
        status = "OK" if not r.errors else "PARTIAL" if r.days_succeeded > 0 else "FAIL"
        print(
            f"  [{status}] {r.source}: "
            f"{r.days_succeeded}/{r.days_attempted} units, "
            f"{r.total_rows:,} rows"
        )
        for err in r.errors:
            print(f"         Error: {err}")
        total_rows += r.total_rows
        total_errors += len(r.errors)

    print(f"\n  Total: {total_rows:,} rows, {total_errors} errors")
    print("=" * 60)
