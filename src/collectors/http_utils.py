"""Shared HTTP utilities with retry/backoff for all collectors."""
from __future__ import annotations

import logging
import time
from typing import Any

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class RateTracker:
    """Per-source rate limit tracker with auto-throttle.

    Tracks 429 responses per source and enforces a cooldown period.
    Call ``check(source)`` before each request; it will sleep if the
    source is rate-limited.

    Usage::

        tracker = RateTracker(default_cooldown=60)
        # before each request:
        tracker.check("tankermap")
        resp = get_with_retry(url, ...)
        # after a 429:
        tracker.record_429("tankermap")
    """

    def __init__(self, default_cooldown: float = 60.0) -> None:
        self.default_cooldown = default_cooldown
        self._cooldowns: dict[str, float] = {}
        self._retry_after: dict[str, float] = {}

    def record_429(self, source: str, retry_after: float | None = None) -> None:
        """Record that ``source`` returned a 429.  Sleeps for retry_after if provided."""
        cooldown = retry_after or self.default_cooldown
        self._retry_after[source] = time.time() + cooldown
        logger.warning(
            "Rate limited by %s — cooldown %.0fs", source, cooldown,
        )
        if retry_after:
            time.sleep(retry_after)

    def check(self, source: str) -> None:
        """Block until ``source`` is no longer rate-limited."""
        unlock = self._retry_after.get(source, 0)
        remaining = unlock - time.time()
        if remaining > 0:
            logger.info(
                "Throttling %s — waiting %.0fs", source, remaining,
            )
            time.sleep(remaining)

    def is_limited(self, source: str) -> bool:
        return time.time() < self._retry_after.get(source, 0)

    def reset(self, source: str | None = None) -> None:
        if source:
            self._retry_after.pop(source, None)
        else:
            self._retry_after.clear()


# Module-level singleton — all collectors share one tracker
_rate_tracker = RateTracker()


def get_session(
    max_retries: int = 3,
    backoff_factor: float = 1.0,
    status_forcelist: tuple[int, ...] = (429, 502, 503, 504),
) -> requests.Session:
    """Create a requests.Session with automatic retry/backoff."""
    session = requests.Session()
    retry = Retry(
        total=max_retries,
        backoff_factor=backoff_factor,
        status_forcelist=status_forcelist,
        allowed_methods=["GET", "POST"],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session


def get_with_retry(
    url: str,
    max_retries: int = 3,
    timeout: int = 60,
    source: str | None = None,
    **kwargs: Any,
) -> requests.Response:
    """GET with manual retry/backoff for full control.

    Parameters
    ----------
    source : str, optional
        Source name for per-source rate limit tracking (e.g. ``"tankermap"``).
        When set, the module-level ``RateTracker`` will auto-throttle if a
        429 is received.
    """
    src = source or url.split("/")[2]
    _rate_tracker.check(src)
    last_exc: Exception | None = None
    for attempt in range(1, max_retries + 1):
        try:
            resp = requests.get(url, timeout=timeout, **kwargs)
            if resp.status_code == 429:
                retry_after = None
                if "Retry-After" in resp.headers:
                    try:
                        retry_after = float(resp.headers["Retry-After"])
                    except ValueError:
                        pass
                _rate_tracker.record_429(src, retry_after)
                continue
            if resp.status_code >= 500:
                wait = min(30, 2 ** attempt)
                logger.warning("Server error %d, retrying in %ds", resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        except requests.ConnectionError as e:
            last_exc = e
            wait = min(30, 2 ** attempt)
            logger.warning("Connection error (attempt %d/%d): %s", attempt, max_retries, e)
            time.sleep(wait)
        except requests.Timeout as e:
            last_exc = e
            wait = min(30, 2 ** attempt)
            logger.warning("Timeout (attempt %d/%d): %s", attempt, max_retries, e)
            time.sleep(wait)

    raise last_exc or requests.ConnectionError(f"Failed after {max_retries} retries")
