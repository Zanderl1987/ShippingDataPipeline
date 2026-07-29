"""Collection duration tracking.

Tracks per-source collection durations and alerts when a collection takes
significantly longer than the running average (z-score > threshold).

Usage:
    from src.monitoring.duration_tracker import DurationTracker

    tracker = DurationTracker()
    anomaly = tracker.record("aisstream", 120.5)
    if anomaly:
        logger.warning("Slow collection: %s", anomaly)
"""
from __future__ import annotations

import logging
from collections import defaultdict

logger = logging.getLogger(__name__)


class DurationTracker:
    """Track and analyse collection durations per source.

    Maintains an in-memory history of durations per source.  After enough
    samples have been collected, each new observation is checked against
    the historical mean and standard deviation.

    Args:
        alert_threshold_sigma: Z-score threshold to flag a slow collection.
        min_samples: Minimum observations before anomaly detection activates.
    """

    def __init__(
        self,
        alert_threshold_sigma: float = 2.0,
        min_samples: int = 5,
    ) -> None:
        self.threshold = alert_threshold_sigma
        self.min_samples = min_samples
        self._durations: dict[str, list[float]] = defaultdict(list)

    def record(self, source: str, duration_seconds: float) -> dict | None:
        """Record a duration and check for anomaly.

        Args:
            source: Source identifier (e.g. ``"aisstream"``).
            duration_seconds: Wall-clock seconds for the collection.

        Returns:
            Anomaly dict if the duration is significantly above average,
            otherwise None.
        """
        self._durations[source].append(duration_seconds)

        history = self._durations[source]
        if len(history) < self.min_samples:
            return None

        mean = sum(history) / len(history)
        if mean == 0:
            return None

        variance = sum((d - mean) ** 2 for d in history) / len(history)
        std = variance ** 0.5
        if std == 0:
            return None

        z_score = (duration_seconds - mean) / std
        if z_score > self.threshold:
            severity = "high" if z_score > 3 else "medium"
            logger.warning(
                "Slow collection for %s: %.1fs (mean=%.1fs, z=%.2f, severity=%s)",
                source,
                duration_seconds,
                mean,
                z_score,
                severity,
            )
            return {
                "source": source,
                "duration_s": round(duration_seconds, 1),
                "mean_s": round(mean, 1),
                "z_score": round(z_score, 2),
                "severity": severity,
            }
        return None

    def get_stats(self, source: str) -> dict[str, float]:
        """Get duration statistics for a source.

        Args:
            source: Source identifier.

        Returns:
            Dict with ``count``, ``last``, ``mean``, ``min``, ``max``.
            Empty dict if no data exists for the source.
        """
        history = self._durations.get(source, [])
        if not history:
            return {}
        return {
            "count": len(history),
            "last": round(history[-1], 1),
            "mean": round(sum(history) / len(history), 1),
            "min": round(min(history), 1),
            "max": round(max(history), 1),
        }

    def get_all_stats(self) -> dict[str, dict[str, float]]:
        """Get duration statistics for all tracked sources.

        Returns:
            Mapping of source name to its stats dict.
        """
        return {src: self.get_stats(src) for src in self._durations}
