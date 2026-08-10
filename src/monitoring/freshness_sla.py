"""Freshness SLA enforcement for shipping data sources.

Defines per-source freshness SLAs (Service Level Agreements) and checks
whether each source is meeting its freshness requirements.  Generates
violations when sources exceed their allowed staleness.

Usage:
    from src.monitoring.freshness_sla import check_freshness_sla, FreshnessSLA

    checker = FreshnessSLA()
    violations = checker.check_all(tracker)
    for v in violations:
        print(f"VIOLATION: {v['source']} is {v['staleness_hours']:.1f}h stale "
              f"(SLA: {v['sla_hours']}h)")
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from src.storage.tracker import SourceTracker

logger = logging.getLogger(__name__)


# ── Per-source SLA definitions ──────────────────────────────────────────────
# Key: source name, Value: max allowed staleness in hours before violation
SOURCE_SLAS: dict[str, float] = {
    # AIS real-time feeds — expected hourly
    "aisstream":           2.0,
    "axiomancer":          2.0,

    # AIS historical — daily refresh
    "gfw":                25.0,
    "mms":                25.0,

    # Port data — daily or weekly
    "world_port_index":   25.0,
    "equasis":            49.0,
    "dma":                25.0,
    "barcelona_port":     25.0,
    "singapore_oceanx":   25.0,

    # Vessel tracking — daily
    "vessel_tracker":     25.0,
    "vesselapi":          25.0,
    "barentswatch":       25.0,

    # Freight rates — daily
    "fbx":                25.0,

    # Economic / macro — weekly
    "eia_petroleum":      49.0,
    "eia_petroleum_api":  49.0,
    "un_comtrade":       169.0,

    # Weather — daily
    "open_meteo":         25.0,

    # Indexes — weekly
    "seafarer_index":    169.0,
    "bdi":               169.0,
    "tankermap":          49.0,
    "hormuz_monitor":     25.0,
}

# Default SLA for sources not explicitly defined
_DEFAULT_SLA_HOURS: float = 49.0


class FreshnessSLA:
    """Check data freshness against defined SLAs.

    Args:
        custom_slas: Override or extend the default SLA definitions.
        default_sla_hours: Fallback SLA for undefined sources.
    """

    def __init__(
        self,
        custom_slas: dict[str, float] | None = None,
        default_sla_hours: float = _DEFAULT_SLA_HOURS,
    ) -> None:
        self.slas = dict(SOURCE_SLAS)
        if custom_slas:
            self.slas.update(custom_slas)
        self.default_sla = default_sla_hours

    def get_sla(self, source: str) -> float:
        """Get the freshness SLA for a source in hours."""
        return self.slas.get(source, self.default_sla)

    def check_source(self, source: str, staleness_hours: float) -> dict[str, Any] | None:
        """Check if a source violates its freshness SLA.

        Args:
            source: Source identifier.
            staleness_hours: Hours since last successful collection.

        Returns:
            Violation dict if the SLA is breached, otherwise None.
        """
        sla = self.get_sla(source)
        if staleness_hours > sla:
            violation_pct = (staleness_hours / sla - 1) * 100
            severity = (
                "critical" if staleness_hours > sla * 2
                else "high" if staleness_hours > sla * 1.5
                else "medium"
            )
            return {
                "source": source,
                "staleness_hours": round(staleness_hours, 1),
                "sla_hours": sla,
                "violation_pct": round(violation_pct, 1),
                "severity": severity,
                "message": (
                    f"{source} is {staleness_hours:.1f}h stale "
                    f"(SLA: {sla:.0f}h, {violation_pct:.0f}% over)"
                ),
                "checked_at": datetime.now().isoformat(),
            }
        return None

    def check_all(self, tracker: SourceTracker) -> list[dict[str, Any]]:
        """Check all sources against their freshness SLAs.

        Args:
            tracker: SourceTracker instance to query staleness.

        Returns:
            List of violation dicts, sorted by severity.
        """
        violations: list[dict[str, Any]] = []

        all_status = tracker.get_all_sources_status()
        for row in all_status.iter_rows(named=True):
            source = row["source"]
            last_collection = row.get("last_collection")

            if last_collection is None:
                # Never collected — always a violation
                sla = self.get_sla(source)
                violations.append({
                    "source": source,
                    "staleness_hours": None,
                    "sla_hours": sla,
                    "violation_pct": None,
                    "severity": "critical",
                    "message": f"{source} has never been collected",
                    "checked_at": datetime.now().isoformat(),
                })
                continue

            if isinstance(last_collection, str):
                last_dt = datetime.fromisoformat(last_collection)
            else:
                last_dt = last_collection
                if isinstance(last_dt, date) and not isinstance(last_dt, datetime):
                    last_dt = datetime.combine(last_dt, datetime.min.time())

            staleness = (datetime.now() - last_dt).total_seconds() / 3600
            violation = self.check_source(source, staleness)
            if violation:
                violations.append(violation)

        severity_order = {"critical": 0, "high": 1, "medium": 2}
        violations.sort(key=lambda v: severity_order.get(v["severity"], 3))
        return violations

    def summary(self, tracker: SourceTracker) -> str:
        """Human-readable SLA compliance summary."""
        violations = self.check_all(tracker)
        all_status = tracker.get_all_sources_status()
        total = len(all_status)
        compliant = total - len(violations)

        lines = [
            f"\n{'='*60}",
            "  FRESHNESS SLA REPORT",
            f"{'='*60}",
            f"  Sources: {total} total, {compliant} compliant, {len(violations)} violations\n",
        ]

        if violations:
            for v in violations:
                icon = {"critical": "X", "high": "!", "medium": "~"}.get(v["severity"], "?")
                lines.append(f"  [{icon}] {v['severity']:8s}  {v['message']}")
        else:
            lines.append("  All sources within SLA.")

        return "\n".join(lines)
