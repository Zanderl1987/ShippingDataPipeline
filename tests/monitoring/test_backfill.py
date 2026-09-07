from __future__ import annotations

from datetime import date, timedelta

import pytest

from src.monitoring.backfill import _get_backfill_adapters

# --- 2026-09-07 code review fix ---
#
# Open-Meteo's `past_days` parameter counts back from TODAY, not from an
# arbitrary `start_date` -- it can only ever serve a trailing window ending
# today. The backfill adapter used to compute `past_days` from the requested
# range's length and pass it straight through, so a historical backfill
# request not ending today silently fetched the wrong days while still
# reporting success.

def test_open_meteo_adapter_rejects_range_not_ending_today() -> None:
    adapters = _get_backfill_adapters()
    adapter = adapters["open_meteo"]
    start = date.today() - timedelta(days=20)
    end = date.today() - timedelta(days=10)  # does not end today
    with pytest.raises(ValueError, match="today"):
        adapter(start, end)


def test_open_meteo_adapter_accepts_trailing_window_ending_today() -> None:
    adapters = _get_backfill_adapters()
    adapter = adapters["open_meteo"]
    start = date.today() - timedelta(days=10)
    end = date.today()
    fns = adapter(start, end)
    assert len(fns) == 1


def test_open_meteo_adapter_rejects_window_over_92_days() -> None:
    adapters = _get_backfill_adapters()
    adapter = adapters["open_meteo"]
    start = date.today() - timedelta(days=200)
    end = date.today()
    with pytest.raises(ValueError, match="92"):
        adapter(start, end)
