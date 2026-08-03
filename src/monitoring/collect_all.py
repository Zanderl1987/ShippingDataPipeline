"""Collection orchestrator for all data sources."""
from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from src.monitoring.notify import (
    Notifier,
    notify_collection_error,
    notify_collection_success,
    notify_quality_warning,
)
from src.monitoring.quality import check_quality_thresholds, get_quality_report
from src.storage.tracker import SourceTracker

logger = logging.getLogger(__name__)


@dataclass
class CollectorDef:
    """Definition of a collector to run."""

    name: str
    collect_fn: Callable[..., Any]
    requires_key: str | None = None
    schedule: str = "daily"  # daily, weekly, ondemand


@dataclass
class CollectionResult:
    """Result of running a collection."""

    source: str
    success: bool
    skipped: bool = False
    rows_fetched: int = 0
    rows_written: int = 0
    duration_ms: int = 0
    error: str | None = None


@dataclass
class CollectionReport:
    """Summary of a full collection run."""

    started_at: datetime
    completed_at: datetime | None = None
    results: list[CollectionResult] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)

    @property
    def succeeded(self) -> int:
        return sum(1 for r in self.results if r.success and not r.skipped)

    @property
    def failed(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def total_rows(self) -> int:
        return sum(r.rows_written for r in self.results)


def get_collectors() -> list[CollectorDef]:
    """Get all available collectors.

    Returns:
        List of CollectorDef for each data source.
    """
    collectors = []

    # No-auth collectors (always available)
    try:
        from src.collectors.axiomancer import collect_global_snapshot
        collectors.append(
            CollectorDef(
                name="axiomancer",
                collect_fn=lambda: collect_global_snapshot(),
                schedule="daily",
            )
        )
    except ImportError:
        logger.warning("axiomancer collector not available")

    try:
        from src.collectors.eagle_intelligence import collect_chokepoint_status
        collectors.append(
            CollectorDef(
                name="eagle_intelligence",
                collect_fn=collect_chokepoint_status,
                schedule="daily",
            )
        )
    except ImportError as e:
        logger.debug("eagle_intelligence collector not available: %s", e)

    try:
        from src.collectors.open_meteo import collect_marine
        collectors.append(
            CollectorDef(
                name="open_meteo",
                collect_fn=lambda: collect_marine(latitude=1.264, longitude=103.82),  # Singapore
                schedule="daily",
            )
        )
    except ImportError as e:
        logger.debug("open_meteo collector not available: %s", e)

    try:
        from src.collectors.imf_portwatch import collect_chokepoint_transits
        collectors.append(
            CollectorDef(
                name="imf_portwatch",
                collect_fn=collect_chokepoint_transits,
                schedule="daily",
            )
        )
    except ImportError:
        logger.warning("imf_portwatch collector not available")

    try:
        from src.collectors.tankermap import collect_vessels
        collectors.append(
            CollectorDef(
                name="tankermap",
                collect_fn=collect_vessels,
                schedule="daily",
            )
        )
    except ImportError:
        logger.warning("tankermap collector not available")

    try:
        from src.collectors.jodi_oil import collect_primary
        collectors.append(
            CollectorDef(
                name="jodi_oil",
                collect_fn=collect_primary,
                schedule="weekly",
            )
        )
    except ImportError:
        logger.warning("jodi_oil collector not available")

    # Key-required collectors
    try:
        from src.collectors.global_fishing_watch import collect_events
        collectors.append(
            CollectorDef(
                name="global_fishing_watch",
                collect_fn=lambda: collect_events(
                    start_date=(date.today() - timedelta(days=1)).isoformat(),
                    end_date=date.today().isoformat(),
                ),
                requires_key="gfw_api_token",
                schedule="daily",
            )
        )
    except ImportError:
        logger.warning("global_fishing_watch collector not available")

    try:
        from src.collectors.vesselapi import collect_port_events
        collectors.append(
            CollectorDef(
                name="vesselapi",
                collect_fn=lambda: collect_port_events(limit=50),
                requires_key="vesselapi_api_key",
                schedule="daily",
            )
        )
    except ImportError:
        logger.warning("vesselapi collector not available")

    try:
        from src.collectors.shiplookup import collect_ship_search
        collectors.append(
            CollectorDef(
                name="shiplookup",
                collect_fn=collect_ship_search,
                requires_key="shiplookup_api_key",
                schedule="weekly",
            )
        )
    except ImportError:
        logger.warning("shiplookup collector not available")

    try:
        from src.collectors.un_comtrade import collect_trade_data
        collectors.append(
            CollectorDef(
                name="un_comtrade",
                collect_fn=lambda: collect_trade_data(reporter_code=156),  # China
                requires_key="un_comtrade_api_key",
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.debug("un_comtrade collector not available: %s", e)

    try:
        from src.collectors.hormuz_monitor import collect_oil_prices
        collectors.append(
            CollectorDef(
                name="hormuz_monitor",
                collect_fn=collect_oil_prices,
                requires_key="hormuz_api_key",
                schedule="daily",
            )
        )
    except ImportError:
        logger.warning("hormuz_monitor collector not available")

    try:
        from src.collectors.eia_petroleum import collect_weekly_stocks
        collectors.append(
            CollectorDef(
                name="eia_petroleum",
                collect_fn=collect_weekly_stocks,
                requires_key="eia_api_key",
                schedule="weekly",
            )
        )
    except ImportError:
        logger.warning("eia_petroleum collector not available")

    # dma, fbx, barcelona_port, singapore_oceanx, equasis were removed here
    # 2026-08-03: all five target endpoints confirmed dead (404 / login-wall)
    # per staging/SESSION_NOTES.md Session 15 audit — they were reporting
    # false [OK] with 0 rows on every run. See US_DOMESTIC_FREIGHT_SOURCES.md
    # equivalent audit note in PIPELINE_STATUS_AND_TASKS.md for revisit criteria.

    # ── Previously-orphaned collectors, wired in 2026-08-03 ─────────────────
    # (built and tested, but never registered here — see Session 15 audit)

    try:
        from src.collectors.noaa_marinecadastre import collect_bulk_download
        _prev_month = date.today().replace(day=1) - timedelta(days=1)
        collectors.append(
            CollectorDef(
                name="noaa_marinecadastre",
                collect_fn=lambda: collect_bulk_download(
                    year=_prev_month.year, month=_prev_month.month
                ),
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.warning("noaa_marinecadastre collector not available: %s", e)

    try:
        from src.collectors.seafarer_index import collect_ships
        collectors.append(
            CollectorDef(
                name="seafarer_index",
                collect_fn=collect_ships,
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.warning("seafarer_index collector not available: %s", e)
    # collect_ports intentionally not wired: the ports endpoint returns
    # HTTP 502 (server-side outage), confirmed live 2026-08-03. Revisit
    # if the upstream API recovers.

    try:
        from src.collectors.barentswatch import collect_latest_positions
        collectors.append(
            CollectorDef(
                name="barentswatch",
                collect_fn=collect_latest_positions,
                requires_key="barentswatch_token",
                schedule="daily",
            )
        )
    except ImportError as e:
        logger.warning("barentswatch collector not available: %s", e)

    # aisstream (collect_stream) intentionally not wired: it needs an
    # explicit geographic bounding-box choice (which waters to subscribe
    # to) before it can run — an arbitrary pick would burn free-tier API
    # quota on the wrong region. Needs a decision, not a default.

    return collectors


def run_collector(
    collector: CollectorDef,
    tracker: SourceTracker,
    notifier: Notifier,
    force: bool = False,
) -> CollectionResult:
    """Run a single collector.

    Args:
        collector: Collector definition.
        tracker: Source tracker for recording results.
        notifier: Notifier for sending alerts.
        force: If True, run even if recently collected.

    Returns:
        CollectionResult with success/failure details.
    """
    import time

    # Check if we have the required API key
    if collector.requires_key:
        from src.config import settings
        key_value = getattr(settings, collector.requires_key, None)
        if not key_value:
            logger.info("Skipping %s (no API key)", collector.name)
            # An unregistered key is a configuration state, not a pipeline
            # failure — the same category as the staleness skip below. Some
            # sources can never be configured (hormuz has no free tier at all),
            # so failing on this would keep the run permanently red.
            return CollectionResult(
                source=collector.name,
                success=True,
                skipped=True,
                error=f"Skipped (no API key: {collector.requires_key})",
            )

    # Check staleness unless forced
    if not force:
        staleness = tracker.get_staleness_hours(collector.name)
        if staleness is not None:
            # Skip if collected in last hour (for daily sources)
            # or last 24 hours (for weekly sources)
            threshold = 24.0 if collector.schedule == "daily" else 168.0
            if staleness < threshold:
                logger.info(
                    "Skipping %s (collected %.1fh ago)",
                    collector.name,
                    staleness,
                )
                return CollectionResult(
                    source=collector.name,
                    success=True,
                    skipped=True,
                    error="Skipped (recently collected)",
                )

    logger.info("Running collector: %s", collector.name)
    start = time.perf_counter()

    try:
        collector.collect_fn()
        duration_ms = int((time.perf_counter() - start) * 1000)

        # Read row counts from tracker (recorded by collector's TimedCollector)
        last = tracker.get_last_collection(collector.name)
        rows_fetched = last["rows_fetched"] if last else 0
        rows_written = last["rows_written"] if last else 0

        result = CollectionResult(
            source=collector.name,
            success=True,
            rows_fetched=rows_fetched,
            rows_written=rows_written,
            duration_ms=duration_ms,
        )

        # Send success notification
        notifier.send(
            notify_collection_success(
                collector.name, rows_fetched, rows_written, duration_ms
            )
        )

        return result

    except Exception as e:
        duration_ms = int((time.perf_counter() - start) * 1000)
        logger.error("Collector %s failed: %s", collector.name, e)

        result = CollectionResult(
            source=collector.name,
            success=False,
            duration_ms=duration_ms,
            error=str(e),
        )

        # Send error notification
        notifier.send(notify_collection_error(collector.name, e))

        return result


def run_all_collectors(
    sources: list[str] | None = None,
    force: bool = False,
    notify: bool = True,
) -> CollectionReport:
    """Run all collectors (or specified sources).

    Args:
        sources: List of source names to collect. None = all.
        force: If True, run even if recently collected.
        notify: If True, send notifications.

    Returns:
        CollectionReport with results for each collector.
    """
    from src.config import settings

    settings.ensure_dirs()

    # Ensure the full schema exists before collecting. Without this, a collector
    # writing to a table that was never created silently persists 0 rows.
    from src.storage.writer import init_db
    init_db().close()

    tracker = SourceTracker()
    notifier = Notifier.from_env() if notify else Notifier()

    report = CollectionReport(started_at=datetime.now())
    collectors = get_collectors()

    for collector in collectors:
        if sources and collector.name not in sources:
            report.skipped.append(collector.name)
            continue

        result = run_collector(collector, tracker, notifier, force)
        report.results.append(result)

    report.completed_at = datetime.now()

    # Check quality and notify if issues
    try:
        quality_report = get_quality_report()
        warnings = check_quality_thresholds(quality_report)
        if warnings and notify:
            notifier.send(notify_quality_warning(warnings))
    except Exception as e:
        logger.error("Quality check failed: %s", e)

    # Log summary
    duration = (report.completed_at - report.started_at).total_seconds()
    logger.info(
        "Collection complete: %d succeeded, %d failed, %d skipped, "
        "%d total rows, %.1fs total duration",
        report.succeeded,
        report.failed,
        len(report.skipped),
        report.total_rows,
        duration,
    )

    return report


def print_collection_report(report: CollectionReport) -> None:
    """Print a formatted collection report."""
    print("\n" + "=" * 60)
    print("COLLECTION REPORT")
    print("=" * 60)
    print(f"Started: {report.started_at:%Y-%m-%d %H:%M:%S}")
    if report.completed_at:
        duration = (report.completed_at - report.started_at).total_seconds()
        print(f"Duration: {duration:.1f}s")

    print(f"\nResults: {report.succeeded} succeeded, {report.failed} failed")
    print(f"Total rows: {report.total_rows:,}")

    if report.results:
        print("\n--- Details ---")
        for result in report.results:
            status = "OK" if result.success else "FAIL"
            duration_str = (
                f"{result.duration_ms / 1000:.1f}s" if result.duration_ms else "N/A"
            )
            print(f"  [{status}] {result.source}: {duration_str}")
            if result.error:
                print(f"         Error: {result.error}")

    if report.skipped:
        print(f"\nSkipped: {', '.join(report.skipped)}")

    print("\n" + "=" * 60)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Run data collection")
    parser.add_argument(
        "--sources",
        nargs="*",
        help="Specific sources to collect (default: all)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force collection even if recently collected",
    )
    parser.add_argument(
        "--no-notify",
        action="store_true",
        help="Disable notifications",
    )
    parser.add_argument(
        "--backfill-start",
        type=date.fromisoformat,
        default=None,
        help="Backfill start date (YYYY-MM-DD). Enables backfill mode.",
    )
    parser.add_argument(
        "--backfill-end",
        type=date.fromisoformat,
        default=None,
        help="Backfill end date (YYYY-MM-DD, inclusive).",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if args.backfill_start and args.backfill_end:
        from src.monitoring.backfill import print_backfill_report, run_backfill
        results = run_backfill(
            start_date=args.backfill_start,
            end_date=args.backfill_end,
            sources=args.sources,
            notify=not args.no_notify,
        )
        print_backfill_report(results)
        sys.exit(0 if all(not r.errors for r in results) else 1)
    else:
        report = run_all_collectors(
            sources=args.sources,
            force=args.force,
            notify=not args.no_notify,
        )
        print_collection_report(report)
        sys.exit(0 if report.failed == 0 else 1)
