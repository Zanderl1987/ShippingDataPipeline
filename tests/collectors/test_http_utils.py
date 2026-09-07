from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.collectors.http_utils import RateTracker, get_with_retry

# --- 2026-09-07 code review fix ---

def test_bare_429_backs_off_before_retrying() -> None:
    """Fixed: RateTracker.record_429() only sleeps when a Retry-After header
    is present. A bare 429 (common on free tiers with no Retry-After) used
    to loop straight back into the next attempt with zero delay."""
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {}

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.raise_for_status = MagicMock()

    with patch("src.collectors.http_utils.requests.get", side_effect=[resp_429, resp_ok]), \
         patch("src.collectors.http_utils.time.sleep") as mock_sleep:
        result = get_with_retry("http://example.com/x", max_retries=3, source="test_bare_429")

    assert result is resp_ok
    assert mock_sleep.called, "a bare 429 with no Retry-After must still back off"


def test_429_with_retry_after_still_honors_it() -> None:
    """Regression guard: the existing Retry-After path must keep working."""
    resp_429 = MagicMock()
    resp_429.status_code = 429
    resp_429.headers = {"Retry-After": "5"}

    resp_ok = MagicMock()
    resp_ok.status_code = 200
    resp_ok.raise_for_status = MagicMock()

    with patch("src.collectors.http_utils.requests.get", side_effect=[resp_429, resp_ok]), \
         patch("src.collectors.http_utils.time.sleep") as mock_sleep:
        result = get_with_retry("http://example.com/y", max_retries=3, source="test_retry_after")

    assert result is resp_ok
    mock_sleep.assert_any_call(5.0)


def test_rate_tracker_record_429_without_retry_after_does_not_sleep_itself() -> None:
    """RateTracker itself still only sleeps when given an explicit
    retry_after -- the backoff for the bare-429 case lives in get_with_retry,
    not here, so this behavior must stay unchanged."""
    tracker = RateTracker()
    with patch("src.collectors.http_utils.time.sleep") as mock_sleep:
        tracker.record_429("some_source")
    mock_sleep.assert_not_called()
