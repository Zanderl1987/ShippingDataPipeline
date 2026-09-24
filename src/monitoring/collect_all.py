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
    curation_errors: list[str] = field(default_factory=list)

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
        from src.collectors.open_meteo import collect_marine, collect_weather
        collectors.append(
            CollectorDef(
                name="open_meteo",
                collect_fn=lambda: collect_marine(latitude=1.264, longitude=103.82),  # Singapore
                schedule="daily",
            )
        )
        collectors.append(
            CollectorDef(
                name="open_meteo_weather",
                collect_fn=lambda: collect_weather(latitude=1.264, longitude=103.82),  # Singapore
                schedule="daily",
            )
        )
    except ImportError as e:
        logger.debug("open_meteo collector not available: %s", e)

    try:
        from src.collectors.erddap_marine import collect_erddap_marine
        collectors.append(
            CollectorDef(
                name="erddap_marine",
                collect_fn=collect_erddap_marine,
                schedule="daily",
            )
        )
    except ImportError as e:
        logger.debug("erddap_marine collector not available: %s", e)

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
        from src.collectors.portwatch_ports import (
            SOURCE_ACTIVITY,
            SOURCE_CHOKEPOINT_PROFILES,
            SOURCE_DISRUPTIONS,
            SOURCE_PROFILES,
            SOURCE_TRADENOW,
            collect_chokepoint_profiles,
            collect_disruption_events,
            collect_port_activity,
            collect_port_profiles,
            collect_trade_nowcast,
        )
        collectors.extend([
            CollectorDef(
                name=SOURCE_ACTIVITY,
                collect_fn=collect_port_activity,
                schedule="daily",
            ),
            CollectorDef(
                name=SOURCE_PROFILES,
                collect_fn=collect_port_profiles,
                schedule="weekly",
            ),
            CollectorDef(
                name=SOURCE_CHOKEPOINT_PROFILES,
                collect_fn=collect_chokepoint_profiles,
                schedule="weekly",
            ),
            CollectorDef(
                name=SOURCE_TRADENOW,
                collect_fn=collect_trade_nowcast,
                schedule="weekly",
            ),
            CollectorDef(
                name=SOURCE_DISRUPTIONS,
                collect_fn=collect_disruption_events,
                schedule="daily",
            ),
        ])
    except ImportError as e:
        logger.warning("portwatch_ports collectors not available: %s", e)

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
        from src.collectors.census_trade import collect_trade_data as collect_census_trade_data
        collectors.append(
            CollectorDef(
                name="census_trade",
                collect_fn=collect_census_trade_data,
                requires_key="census_api_key",
                schedule="monthly",
            )
        )
    except ImportError as e:
        logger.debug("census_trade collector not available: %s", e)

    try:
        from src.collectors.fred_oil import collect_oil_prices as collect_fred_oil_prices
        collectors.append(
            CollectorDef(
                name="fred_oil",
                collect_fn=collect_fred_oil_prices,
                requires_key="fred_api_key",
                schedule="daily",
            )
        )
    except ImportError as e:
        logger.debug("fred_oil collector not available: %s", e)

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
        from src.collectors.noaa_marinecadastre import collect_vessel_tracks
        collectors.append(
            CollectorDef(
                name="noaa_marinecadastre",
                collect_fn=collect_vessel_tracks,
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

    try:
        from src.collectors.digitraffic import (
            collect_locations,
        )
        from src.collectors.digitraffic import (
            collect_port_calls as collect_digitraffic_port_calls,
        )
        from src.collectors.digitraffic import (
            collect_ports as collect_digitraffic_ports,
        )
        from src.collectors.digitraffic import (
            collect_vessels as collect_digitraffic_vessels,
        )
        collectors.append(
            CollectorDef(
                name="digitraffic",
                collect_fn=collect_locations,
                schedule="daily",
            )
        )
        collectors.append(
            CollectorDef(
                name="digitraffic_vessels",
                collect_fn=collect_digitraffic_vessels,
                schedule="weekly",
            )
        )
        collectors.append(
            CollectorDef(
                name="digitraffic_port_calls",
                collect_fn=collect_digitraffic_port_calls,
                schedule="weekly",
            )
        )
        collectors.append(
            CollectorDef(
                name="digitraffic_ports",
                collect_fn=collect_digitraffic_ports,
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.warning("digitraffic collector not available: %s", e)

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

    try:
        from src.collectors.aisstream import CHOKEPOINT_BBOXES, collect_stream
        collectors.append(
            CollectorDef(
                name="aisstream",
                collect_fn=lambda: collect_stream(
                    bbox=CHOKEPOINT_BBOXES, duration_seconds=120
                ),
                requires_key="aisstream_api_key",
                schedule="daily",
            )
        )
    except ImportError as e:
        logger.warning("aisstream collector not available: %s", e)

    try:
        from src.collectors.oilpriceapi import (
            collect_freight_indices,
            collect_oil_prices,
        )
        collectors.append(
            CollectorDef(
                name="oilpriceapi_oil",
                collect_fn=collect_oil_prices,
                requires_key="oilpriceapi_api_key",
                schedule="daily",
            )
        )
        collectors.append(
            CollectorDef(
                name="oilpriceapi_freight",
                collect_fn=collect_freight_indices,
                requires_key="oilpriceapi_api_key",
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.warning("oilpriceapi collector not available: %s", e)

    try:
        from src.collectors.nyfi import collect_nyfi
        collectors.append(
            CollectorDef(
                name="nyfi",
                collect_fn=collect_nyfi,
                requires_key="nyshex_api_key",
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.warning("nyfi collector not available: %s", e)

    try:
        from src.collectors.unlocode import collect_ports as collect_unlocode_ports
        collectors.append(
            CollectorDef(
                name="unlocode_ports",
                collect_fn=collect_unlocode_ports,
                schedule="ondemand",
            )
        )
    except ImportError as e:
        logger.warning("unlocode collector not available: %s", e)

    try:
        from src.collectors.usace_ports import SOURCE as USACE_SOURCE
        from src.collectors.usace_ports import collect_principal_ports
        collectors.append(
            CollectorDef(
                name=USACE_SOURCE,
                collect_fn=collect_principal_ports,
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.warning("usace_principal_ports collector not available: %s", e)

    # ── Port volumes / macro indices (no-auth, Tier-1 pattern-reuse) ──────

    try:
        from src.collectors.port_la import collect_port_la_data
        collectors.append(
            CollectorDef(
                name="port_la",
                collect_fn=collect_port_la_data,
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.warning("port_la collector not available: %s", e)

    try:
        from src.collectors.gscpi import collect_gscpi_data
        collectors.append(
            CollectorDef(
                name="nyfed_gscpi",
                collect_fn=collect_gscpi_data,
                schedule="monthly",
            )
        )
    except ImportError as e:
        logger.warning("gscpi collector not available: %s", e)

    try:
        from src.collectors.eurostat_maritime import collect_eurostat_maritime_data
        collectors.append(
            CollectorDef(
                name="eurostat_maritime",
                collect_fn=collect_eurostat_maritime_data,
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.warning("eurostat_maritime collector not available: %s", e)

    try:
        from src.collectors.eurostat_comext import collect_comext_data
        collectors.append(
            CollectorDef(
                name="eurostat_comext",
                collect_fn=lambda: collect_comext_data(reporter="DE"),  # Germany
                schedule="monthly",
            )
        )
    except ImportError as e:
        logger.warning("eurostat_comext collector not available: %s", e)

    # ── Sanctions / disruption / port benchmarks (no-auth) ────────────────

    try:
        from src.collectors.ofac_sanctions import collect_ofac_sanctions_data
        collectors.append(
            CollectorDef(
                name="ofac_sdn",
                collect_fn=collect_ofac_sanctions_data,
                schedule="daily",
            )
        )
    except ImportError as e:
        logger.warning("ofac_sanctions collector not available: %s", e)

    try:
        from src.collectors.noaa_storms import collect_noaa_storms_data
        collectors.append(
            CollectorDef(
                name="noaa_storms",
                collect_fn=collect_noaa_storms_data,
                schedule="weekly",
            )
        )
    except ImportError as e:
        logger.warning("noaa_storms collector not available: %s", e)

    try:
        from src.collectors.singapore_mpa import collect_singapore_mpa_data
        collectors.append(
            CollectorDef(
                name="singapore_mpa",
                collect_fn=collect_singapore_mpa_data,
                schedule="monthly",
            )
        )
    except ImportError as e:
        logger.warning("singapore_mpa collector not available: %s", e)

    try:
        from src.collectors.bts_air_cargo import collect_bts_air_cargo_data
        collectors.append(
            CollectorDef(
                name="bts_t100",
                collect_fn=collect_bts_air_cargo_data,
                schedule="monthly",
            )
        )
    except ImportError as e:
        logger.warning("bts_air_cargo collector not available: %s", e)

    # FreightPulse port congestion, freight rates, and carriers were retired
    # 2026-09-24. Its API changed: congestion is now IMF PortWatch port calls
    # (rebuilt from port_activity as port_congestion_proxy in curation), rates
    # are US trucking only, and carriers is a US trucking name search. Their
    # tables keep the history collected before the change.
    # freightpulse_disruptions was retired the same day: the endpoint now
    # repackages NWS (US weather), GDACS and USGS natural-hazard alerts, with
    # no route or rate impact; GDACS events are covered by PortWatch
    # disruption_events. Its supply_chain_disruptions table never held rows.

    try:
        from src.collectors.freightpulse_fuel import collect_fuel_prices
        collectors.append(
            CollectorDef(
                name="freightpulse_fuel",
                collect_fn=collect_fuel_prices,
                schedule="daily",
            )
        )
    except ImportError as e:
        logger.warning("freightpulse_fuel collector not available: %s", e)

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
    before = tracker.get_last_collection(collector.name)
    start = time.perf_counter()

    try:
        collector.collect_fn()
        duration_ms = int((time.perf_counter() - start) * 1000)

        # Read row counts from tracker (recorded by collector's TimedCollector)
        last = tracker.get_last_collection(collector.name)
        if last is None or last == before:
            # The collector recorded under some other name, so the counts
            # below would be 0 or a previous run's (erddap_marine, fred_oil
            # and open_meteo_weather reported "0 rows" for weeks this way).
            logger.warning(
                "Collector %s recorded no tracker row under its registered name; "
                "reported row counts are not from this run",
                collector.name,
            )
            last = None
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
    run_curation: bool = True,
) -> CollectionReport:
    """Run all collectors (or specified sources).

    Args:
        sources: List of source names to collect. None = all.
        force: If True, run even if recently collected.
        notify: If True, send notifications.
        run_curation: If True (default), run dedup/validation/enrichment
            (src.curation.pipeline.run_curation) after collection. Without
            this, raw collector output is never deduped or validated in
            production -- CI's collect.yml only calls this function, so
            skipping it here meant curation silently never ran at all.

    Returns:
        CollectionReport with results for each collector.
    """
    from src.config import settings

    settings.ensure_dirs()

    # Ensure the full schema exists before collecting. Without this, a collector
    # writing to a table that was never created silently persists 0 rows.
    from src.monitoring.quality_alerts import take_baseline
    from src.storage.writer import init_db
    conn = init_db()
    try:
        baseline = take_baseline(conn)
    finally:
        conn.close()

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

    if run_curation:
        try:
            from src.curation.pipeline import run_curation as _run_curation
            curation_result = _run_curation(tracker=tracker)
            report.curation_errors = list(curation_result.errors)
            logger.info(
                "Curation complete: %d rows deduped, validation %s",
                curation_result.total_deduped,
                "passed" if curation_result.validation_passed else "FAILED",
            )
            if not curation_result.validation_passed and notify:
                failed = [
                    r.table for r in curation_result.validation_reports if not r.passed
                ]
                notifier.send(
                    notify_quality_warning(
                        [f"Curation validation failed for: {', '.join(failed)}"]
                    )
                )
        except Exception as e:
            logger.error("Curation failed: %s", e)
            report.curation_errors.append(str(e))

    # Alert on what this run changed (see quality_alerts for why not on
    # overall null rates).
    try:
        from src.monitoring.quality_alerts import check_run, write_github_annotations
        conn = init_db()
        try:
            alerts = check_run(conn, baseline)
        finally:
            conn.close()
        if alerts:
            logger.warning("%d data alerts", len(alerts))
            write_github_annotations(alerts)
            if notify:
                notifier.send(notify_quality_warning([a.message for a in alerts]))
        else:
            logger.info("Data alerts: none")
    except Exception as e:
        logger.error("Data alert checks failed: %s", e)

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


#: Exit status when the run finished but some collectors or curation failed.
#: CI still publishes on it (every successful collector's rows are in the
#: database), then fails the job. An uncaught exception exits 1, which CI does
#: not publish on, so this must not be 0 or 1.
EXIT_PARTIAL = 2


def exit_code(report: CollectionReport) -> int:
    """0 if everything succeeded, EXIT_PARTIAL if anything failed."""
    if report.failed or report.curation_errors:
        return EXIT_PARTIAL
    return 0


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

    if report.curation_errors:
        print("\n--- Curation errors ---")
        for err in report.curation_errors:
            print(f"  - {err}")

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
        sys.exit(exit_code(report))
