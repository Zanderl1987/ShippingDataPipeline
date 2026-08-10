"""Monitoring alerts for the shipping pipeline.

Provides volume anomaly detection, cross-source consistency checks,
and notification deduplication to avoid alert fatigue.

Usage:
    from src.monitoring.alerts import check_all_monitoring

    results = check_all_monitoring(tracker, source_results)
    if results["volume_anomalies"]:
        send_alert(results["volume_anomalies"])
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from typing import Any

from src.storage.tracker import SourceTracker

logger = logging.getLogger(__name__)


class AlertDedup:
    """Deduplicate alerts so the same issue doesn't fire every run.

    Each alert is identified by a string key.  After firing, the key is
    suppressed for ``cooldown_seconds``.

    Args:
        cooldown_seconds: Minimum seconds between repeated alerts for the same key.
    """

    def __init__(self, cooldown_seconds: float = 3600.0) -> None:
        self.cooldown = cooldown_seconds
        self._last_fired: dict[str, float] = {}

    def should_fire(self, alert_key: str) -> bool:
        """Return True if enough time has passed since the last firing."""
        now = time.time()
        last = self._last_fired.get(alert_key, 0.0)
        if now - last < self.cooldown:
            return False
        self._last_fired[alert_key] = now
        return True


class VolumeAnomalyDetector:
    """Z-score detection on row counts per source.

    Maintains a rolling in-memory window of historical row counts and flags
    any new observation whose z-score exceeds the configured threshold.

    Args:
        threshold_sigma: Number of standard deviations to trigger an alert.
        window: Maximum number of historical observations used for stats.
    """

    def __init__(
        self,
        threshold_sigma: float = 2.0,
        window: int = 30,
    ) -> None:
        self.threshold = threshold_sigma
        self.window = window
        self._history: dict[str, list[int]] = defaultdict(list)

    def check(self, source: str, row_count: int) -> dict[str, Any] | None:
        """Check if *row_count* is anomalous for *source*.

        Returns:
            Dict with source, row_count, mean, z_score, and severity if
            anomalous; otherwise None.
        """
        history = self._history[source]
        history.append(row_count)

        # Need at least a handful of observations for meaningful stats.
        if len(history) < 5:
            return None

        recent = history[-self.window :]
        mean = sum(recent) / len(recent)
        if mean == 0:
            return None

        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        std = variance ** 0.5
        if std == 0:
            return None

        z_score = (row_count - mean) / std
        if abs(z_score) > self.threshold:
            return {
                "source": source,
                "row_count": row_count,
                "mean": round(mean, 1),
                "z_score": round(z_score, 2),
                "severity": "high" if abs(z_score) > 3 else "medium",
            }
        return None


class CrossSourceConsistency:
    """Check for inconsistencies between related data sources.

    Rules define groups of sources that should agree (both present or both
    absent).  A warning is emitted when the rule is violated.
    """

    RULES: list[dict[str, Any]] = [
        {
            "sources": ["tankermap", "aisstream"],
            "check": "both_or_neither",
        },
        {
            "sources": ["eia_petroleum", "hormuz_monitor"],
            "check": "both_have_data",
        },
    ]

    def check(self, source_results: dict[str, int]) -> list[dict[str, Any]]:
        """Evaluate all consistency rules against observed row counts.

        Args:
            source_results: Mapping of source name to rows collected this run.

        Returns:
            List of warning dicts for violated rules.
        """
        warnings: list[dict[str, Any]] = []

        for rule in self.RULES:
            sources = rule["sources"]
            counts = [source_results.get(s, 0) for s in sources]
            check_type = rule["check"]

            if check_type == "both_or_neither":
                has_data = [c > 0 for c in counts]
                if any(has_data) and not all(has_data):
                    detail_parts = [
                        f"{s}={'has' if has else 'no'} data"
                        for s, has in zip(sources, has_data)
                    ]
                    warnings.append({
                        "rule": check_type,
                        "sources": sources,
                        "counts": dict(zip(sources, counts)),
                        "message": f"Inconsistent data: {', '.join(detail_parts)}",
                    })

            elif check_type == "both_have_data":
                if all(c == 0 for c in counts):
                    warnings.append({
                        "rule": check_type,
                        "sources": sources,
                        "counts": dict(zip(sources, counts)),
                        "message": f"Both {sources[0]} and {sources[1]} returned no data",
                    })

        return warnings


def check_all_monitoring(
    tracker: SourceTracker,
    source_results: dict[str, int],
) -> dict[str, Any]:
    """Run all monitoring checks and return consolidated results.

    Args:
        tracker: SourceTracker instance for freshness queries.
        source_results: Mapping of source name to rows collected this run.

    Returns:
        Dict with ``volume_anomalies``, ``consistency_warnings``, and
        ``freshness`` keys.
    """
    dedup = AlertDedup()
    volume_detector = VolumeAnomalyDetector()
    consistency = CrossSourceConsistency()

    results: dict[str, Any] = {
        "volume_anomalies": [],
        "consistency_warnings": [],
        "freshness": {},
    }

    # ── Volume anomaly checks ────────────────────────────────────────────
    for source, count in source_results.items():
        anomaly = volume_detector.check(source, count)
        if anomaly and dedup.should_fire(f"volume:{source}"):
            results["volume_anomalies"].append(anomaly)
            logger.warning("Volume anomaly detected: %s", anomaly)

    # ── Cross-source consistency ─────────────────────────────────────────
    consistency_warnings = consistency.check(source_results)
    for warning in consistency_warnings:
        dedup_key = f"consistency:{warning['rule']}:{tuple(warning['sources'])}"
        if dedup.should_fire(dedup_key):
            results["consistency_warnings"].append(warning)
            logger.warning("Consistency warning: %s", warning)

    # ── Freshness checks ─────────────────────────────────────────────────
    all_status = tracker.get_all_sources_status()
    for row in all_status.iter_rows(named=True):
        source = row["source"]
        last_collection = row.get("last_collection")
        if last_collection is None:
            continue

        from datetime import date, datetime

        if isinstance(last_collection, str):
            last_dt = datetime.fromisoformat(last_collection)
        else:
            last_dt = last_collection
            if isinstance(last_dt, date) and not isinstance(last_dt, datetime):
                last_dt = datetime.combine(last_dt, datetime.min.time())

        staleness_hours = (datetime.now() - last_dt).total_seconds() / 3600
        if staleness_hours > 168:  # > 7 days
            if dedup.should_fire(f"freshness:{source}"):
                results["freshness"][source] = {
                    "last_collection": str(last_collection),
                    "staleness_hours": round(staleness_hours, 1),
                }

    return results
