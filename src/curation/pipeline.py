"""Curation pipeline orchestrator."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import duckdb

from src.curation.cleaning import clean_ais_positions, clean_ports
from src.curation.dedup import (
    deduplicate_ais_positions,
    deduplicate_ports,
    deduplicate_vessels,
)
from src.curation.enrichment import (
    create_curated_ais_positions,
    create_curated_vessels,
    create_port_congestion_proxy,
)
from src.curation.validation import (
    ValidationReport,
    run_all_validations,
)
from src.storage.tracker import SourceTracker
from src.storage.writer import get_db_path

logger = logging.getLogger(__name__)


@dataclass
class CurationResult:
    """Result of a curation run."""

    dedup_results: dict[str, int] = field(default_factory=dict)
    cleaning_results: dict[str, int] = field(default_factory=dict)
    validation_reports: list[ValidationReport] = field(default_factory=list)
    enrichment_results: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def total_deduped(self) -> int:
        return sum(self.dedup_results.values())

    @property
    def validation_passed(self) -> bool:
        return all(r.passed for r in self.validation_reports)


def run_deduplication(
    conn: duckdb.DuckDBPyConnection, errors: list[str] | None = None
) -> dict[str, int]:
    """Run deduplication on all applicable tables.

    Per-table failures are logged AND appended to `errors` (when given) --
    without that, a caller checking `CurationResult.errors` to decide whether
    a run is trustworthy would see an empty list even though a table's dedup
    silently never ran.
    """
    results = {}

    try:
        results["ais_positions"] = deduplicate_ais_positions(conn)
    except Exception as e:
        logger.error("Dedup ais_positions failed: %s", e)
        if errors is not None:
            errors.append(f"Dedup ais_positions failed: {e}")

    try:
        results["vessels"] = deduplicate_vessels(conn)
    except Exception as e:
        logger.error("Dedup vessels failed: %s", e)
        if errors is not None:
            errors.append(f"Dedup vessels failed: {e}")

    try:
        results["ports"] = deduplicate_ports(conn)
    except Exception as e:
        logger.error("Dedup ports failed: %s", e)
        if errors is not None:
            errors.append(f"Dedup ports failed: {e}")

    return results


def run_cleaning(
    conn: duckdb.DuckDBPyConnection, errors: list[str] | None = None
) -> dict[str, int]:
    """NULL impossible values and drop unusable rows (src.curation.cleaning)."""
    results = {}
    for table, fn in (("ais_positions", clean_ais_positions), ("ports", clean_ports)):
        try:
            results[table] = fn(conn)
        except Exception as e:
            logger.error("Cleaning %s failed: %s", table, e)
            if errors is not None:
                errors.append(f"Cleaning {table} failed: {e}")
    return results


def run_validation(conn: duckdb.DuckDBPyConnection) -> list[ValidationReport]:
    """Run validation on all tables."""
    return run_all_validations(conn)


def run_enrichment(
    conn: duckdb.DuckDBPyConnection, errors: list[str] | None = None
) -> dict[str, int]:
    """Run enrichment to create curated tables."""
    results = {}

    try:
        results["curated_ais_positions"] = create_curated_ais_positions(conn)
    except Exception as e:
        logger.error("Enrichment ais_positions failed: %s", e)
        if errors is not None:
            errors.append(f"Enrichment ais_positions failed: {e}")

    try:
        results["curated_vessels"] = create_curated_vessels(conn)
    except Exception as e:
        logger.error("Enrichment vessels failed: %s", e)
        if errors is not None:
            errors.append(f"Enrichment vessels failed: {e}")

    try:
        results["port_congestion_proxy"] = create_port_congestion_proxy(conn)
    except Exception as e:
        logger.error("Enrichment port_congestion_proxy failed: %s", e)
        if errors is not None:
            errors.append(f"Enrichment port_congestion_proxy failed: {e}")

    try:
        from src.analytics.chokepoint_daily import create_chokepoint_daily
        results["chokepoint_daily"] = create_chokepoint_daily(conn)
    except Exception as e:
        logger.error("Enrichment chokepoint_daily failed: %s", e)
        if errors is not None:
            errors.append(f"Enrichment chokepoint_daily failed: {e}")

    try:
        from src.analytics.port_weekly import create_port_weekly
        results["port_weekly"] = create_port_weekly(conn)
    except Exception as e:
        logger.error("Enrichment port_weekly failed: %s", e)
        if errors is not None:
            errors.append(f"Enrichment port_weekly failed: {e}")

    return results


def run_curation(
    skip_enrichment: bool = False,
    tracker: SourceTracker | None = None,
) -> CurationResult:
    """Run the full curation pipeline.

    Steps:
    1. Deduplication - Remove duplicate records
    2. Cleaning - NULL impossible values, drop unusable rows
    3. Validation - Check data quality
    4. Enrichment - Create curated tables with joins

    Args:
        skip_enrichment: If True, skip the enrichment step.
        tracker: Optional SourceTracker for audit trail.

    Returns:
        CurationResult with details of what was done.
    """
    import time
    from datetime import datetime

    from src.storage.lineage import LineageTracker

    result = CurationResult()
    db_path = get_db_path()
    conn = duckdb.connect(str(db_path))
    _tracker = tracker or SourceTracker()
    _started_at = datetime.now()
    _start_perf = time.perf_counter()

    lineage = LineageTracker()
    _lineage_id: int | None = None
    try:
        _lineage_id = lineage.record_event(
            event_type="curation",
            source="curation_pipeline",
            started_at=_started_at,
            config={"skip_enrichment": skip_enrichment},
        )
    except Exception as e:
        logger.debug("Lineage recording failed: %s", e)

    try:
        logger.info("Starting curation pipeline")

        logger.info("Step 1: Deduplication")
        result.dedup_results = run_deduplication(conn, errors=result.errors)

        logger.info("Step 2: Cleaning")
        result.cleaning_results = run_cleaning(conn, errors=result.errors)

        logger.info("Step 3: Validation")
        result.validation_reports = run_validation(conn)

        if not skip_enrichment:
            logger.info("Step 4: Enrichment")
            result.enrichment_results = run_enrichment(conn, errors=result.errors)

        logger.info("Curation pipeline complete")
        logger.info("Deduped %d total rows", result.total_deduped)
        logger.info(
            "Validation: %d/%d checks passed",
            sum(1 for r in result.validation_reports if r.passed),
            len(result.validation_reports),
        )

        if _lineage_id is not None:
            try:
                duration_ms = int((time.perf_counter() - _start_perf) * 1000)
                lineage.update_event(
                    _lineage_id,
                    completed_at=datetime.now(),
                    status="success",
                    rows_output=result.total_deduped,
                    duration_ms=duration_ms,
                    metadata={
                        "dedup_results": result.dedup_results,
                        "cleaning_results": result.cleaning_results,
                        "validation_passed": result.validation_passed,
                        "enrichment_results": result.enrichment_results,
                    },
                )
            except Exception as e:
                logger.debug("Lineage update failed: %s", e)

    except Exception as e:
        result.errors.append(str(e))
        logger.error("Curation pipeline failed: %s", e)

        if _lineage_id is not None:
            try:
                duration_ms = int((time.perf_counter() - _start_perf) * 1000)
                lineage.update_event(
                    _lineage_id,
                    completed_at=datetime.now(),
                    status="error",
                    duration_ms=duration_ms,
                    error_message=str(e),
                )
            except Exception:
                pass
    finally:
        conn.close()
        lineage.close()

    return result


def print_curation_report(result: CurationResult) -> None:
    """Print a formatted curation report."""
    print("\n" + "=" * 60)
    print("CURATION REPORT")
    print("=" * 60)

    print("\n--- Deduplication ---")
    for table, count in result.dedup_results.items():
        print(f"  {table}: {count} rows removed")

    print("\n--- Validation ---")
    for report in result.validation_reports:
        status = "PASS" if report.passed else "FAIL"
        print(f"  {report.table}: {status} ({report.passed_checks}/{report.total_checks})")
        for r in report.results:
            if not r.passed:
                print(f"    - {r.check}: {r.message}")

    if result.enrichment_results:
        print("\n--- Enrichment ---")
        for table, count in result.enrichment_results.items():
            print(f"  {table}: {count} rows created")

    if result.errors:
        print("\n--- Errors ---")
        for error in result.errors:
            print(f"  - {error}")

    print("\n" + "=" * 60)
