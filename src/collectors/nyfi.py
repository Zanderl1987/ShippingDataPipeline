"""Collect the NYSHEX NYFI container-freight index into freight_rates.

NYSHEX publishes the New York Container Index (NYFI), a weekly composite
freight index covering major trade lanes. Auth is `Authorization: ApiKey <key>`
(env `NYSHEX_API_KEY`, free account). The list endpoint accepts a
startDate/endDate window in ISO8601 and returns the published weekly readings.

The response shape is documented but not publicly browsable without a key, so
the parser is written defensively against the documented schema:

    {
      "timeframe": "2026-34",          # ISO year-week (YYYY-WW)
      "publishDate": "2026-08-20",
      "indices": [                     # trade lanes / sub-indices
        {"code": "NYFI-TPE", "name": "Trans-Pacific Eastbound",
         "lane": "Shanghai-Los Angeles", "value": 2140.5, "unit": "USD/FEU"},
        ...
      ]
    }

Missing/null fields are tolerated; a raw response sample is logged at DEBUG.
Docs: https://www.nyshex.com/nyfi
"""
from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Any

import polars as pl

from src.collectors.http_utils import get_with_retry
from src.config import settings
from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

BASE_URL = "https://dataapi.nyshex.com"
LIST_URL = f"{BASE_URL}/index/v1/list"

SOURCE = "nyfi"

# Weekly cadence — fetch a rolling 7-day window so re-runs stay idempotent.
WINDOW_DAYS = 7


def _get_auth_headers() -> dict[str, str]:
    key = getattr(settings, "nyshex_api_key", None)
    if not key:
        raise RuntimeError("NYSHEX_API_KEY is not set in configuration")
    return {"Authorization": f"ApiKey {key}"}


def fetch_index(start_date: str, end_date: str) -> dict[str, Any]:
    """Fetch published NYFI index readings for the given ISO8601 window."""
    resp = get_with_retry(
        LIST_URL,
        params={"startDate": start_date, "endDate": end_date},
        headers=_get_auth_headers(),
        timeout=30,
        source=SOURCE,
    )
    data: dict[str, Any] = resp.json()
    sample = str(data)[:500]
    logger.debug("NYFI raw response sample: %s", sample)
    return data


def _extract_indices(data: dict[str, Any]) -> tuple[str | None, str | None, list[dict[str, Any]]]:
    """Normalize the response into (timeframe, publish_date, index records).

    Tolerates both the flat shape (`indices` at the top level) and a wrapped
    shape (`data.indices`). Non-dict entries are dropped.
    """
    if not isinstance(data, dict):
        return None, None, []

    raw = data.get("data")
    payload: dict[str, Any] = raw if isinstance(raw, dict) else data
    timeframe = payload.get("timeframe")
    publish_date = payload.get("publishDate") or payload.get("publish_date")
    indices = payload.get("indices")
    if not isinstance(indices, list):
        return (
            timeframe if isinstance(timeframe, str) else None,
            publish_date if isinstance(publish_date, str) else None,
            [],
        )
    return (
        timeframe if isinstance(timeframe, str) else None,
        publish_date if isinstance(publish_date, str) else None,
        [i for i in indices if isinstance(i, dict)],
    )


def _timeframe_to_date(timeframe: str) -> date | None:
    """Convert an ISO year-week ('YYYY-WW' or 'YYYYWW') to the week's Monday."""
    match = re.match(r"^(\d{4})-?W?(\d{1,2})$", timeframe)
    if not match:
        return None
    try:
        return date.fromisocalendar(int(match.group(1)), int(match.group(2)), 1)
    except ValueError:
        return None


def _split_lane(lane: Any) -> tuple[str | None, str | None]:
    """Split a lane string like 'Shanghai - Los Angeles' into origin/destination."""
    if not isinstance(lane, str) or not lane.strip():
        return None, None
    parts = re.split(r"\s+[-–>]+\s+", lane.strip())
    origin = parts[0].strip() or None
    destination = parts[1].strip() if len(parts) > 1 and parts[1].strip() else None
    return origin, destination


def _index_to_date(idx: dict[str, Any], publish_date: str | None) -> date:
    """Resolve a reading date: entry date > response publishDate > today."""
    for candidate in (idx.get("date"), idx.get("publishedAt"), publish_date):
        if isinstance(candidate, str) and candidate[:10]:
            try:
                return date.fromisoformat(candidate[:10])
            except ValueError:
                continue
    return date.today()


def _parse_indices(data: dict[str, Any]) -> pl.DataFrame:
    """Parse the NYFI payload into per-index rows (freight_rates).

    Composite indices carry no container type; lane strings are split into
    origin/destination where they follow '<origin> - <destination>'.
    """
    timeframe, publish_date, indices = _extract_indices(data)
    if not indices:
        return pl.DataFrame()

    week_date = _timeframe_to_date(timeframe) if timeframe else None

    records: list[dict[str, Any]] = []
    for idx in indices:
        name = idx.get("name") or idx.get("code")
        value = idx.get("value")
        if not isinstance(name, str) or not name or value is None:
            continue
        code = idx.get("code") or re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
        origin, destination = _split_lane(idx.get("lane"))
        rate_date = week_date or _index_to_date(idx, publish_date)
        records.append({
            "route_code": code,
            "route_name": f"{name} ({timeframe})" if timeframe else name,
            "origin": origin,
            "destination": destination,
            "container_type": idx.get("containerType"),
            "rate_usd": value,
            "rate_date": rate_date,
        })

    if not records:
        return pl.DataFrame()

    df = pl.DataFrame(records)
    df = df.with_columns(pl.lit(SOURCE).alias("source"))
    return df


def collect_nyfi(tracker: SourceTracker | None = None) -> int:
    """Collect the latest NYFI weekly index readings into freight_rates.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, "nyfi") as tc:
        if not getattr(settings, "nyshex_api_key", None):
            logger.warning("NYSHEX_API_KEY not set, skipping NYFI collection")
            return 0

        end = date.today()
        start = end - timedelta(days=WINDOW_DAYS)
        raw = fetch_index(start.isoformat(), end.isoformat())
        df = _parse_indices(raw)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.info("No NYFI index data returned for the last %d days", WINDOW_DAYS)
            return 0

        logger.info("Writing %d NYFI freight index records", df.height)
        count = write_raw(SOURCE, df, table_name="freight_rates")
        tc.rows_written = count
        return count
