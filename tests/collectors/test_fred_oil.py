from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.fred_oil import parse_fred_oil_prices

WTI_OBSERVATIONS = [
    {"date": "2026-01-02", "value": "72.50"},
    {"date": "2026-01-03", "value": "73.10"},
    {"date": "2026-01-04", "value": "."},  # FRED's missing-observation marker
]

BRENT_OBSERVATIONS = [
    {"date": "2026-01-02", "value": "76.20"},
    {"date": "2026-01-03", "value": "76.80"},
]


@pytest.fixture
def mock_settings(tmp_path: Path) -> None:
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    yield
    settings.data_dir = old_data
    settings.storage_dir = old_storage


def test_parse_fred_oil_prices_merges_series() -> None:
    df = parse_fred_oil_prices({
        "DCOILWTICO": WTI_OBSERVATIONS,
        "DCOILBRENTEU": BRENT_OBSERVATIONS,
    })
    # 2026-01-04 has only a WTI observation, and it's the "." missing marker,
    # so it should be dropped entirely rather than produce an all-null row.
    assert df.height == 2
    by_date = {str(r["price_date"]): r for r in df.to_dicts()}
    assert by_date["2026-01-02"]["wti_usd"] == pytest.approx(72.50)
    assert by_date["2026-01-02"]["brent_usd"] == pytest.approx(76.20)
    assert by_date["2026-01-03"]["wti_usd"] == pytest.approx(73.10)
    assert set(df["source"].unique().to_list()) == {"fred"}


def test_parse_fred_oil_prices_unknown_series_ignored() -> None:
    df = parse_fred_oil_prices({"SOME_OTHER_SERIES": WTI_OBSERVATIONS})
    assert df.height == 0


def test_parse_fred_oil_prices_empty() -> None:
    assert parse_fred_oil_prices({}).height == 0
    assert parse_fred_oil_prices({"DCOILWTICO": []}).height == 0


def test_fetch_series_observations_requires_key() -> None:
    from src.collectors.fred_oil import fetch_series_observations
    from src.config import settings

    old_key = settings.fred_api_key
    settings.fred_api_key = None
    try:
        with pytest.raises(ValueError, match="FRED_API_KEY"):
            fetch_series_observations(
                series_id="DCOILWTICO", start_date="2026-01-01", end_date="2026-01-31",
            )
    finally:
        settings.fred_api_key = old_key


@patch("src.collectors.fred_oil.write_raw")
@patch("src.collectors.fred_oil.fetch_series_observations")
def test_collect_oil_prices(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.side_effect = [WTI_OBSERVATIONS, BRENT_OBSERVATIONS]
    mock_write.return_value = 2

    from src.collectors.fred_oil import collect_oil_prices

    count = collect_oil_prices(start_date="2026-01-01", end_date="2026-01-31")
    assert count == 2
    assert mock_fetch.call_count == 2
    mock_write.assert_called_once()
    written_df = mock_write.call_args[0][1]
    assert written_df.height == 2


@patch("src.collectors.fred_oil.write_raw")
@patch("src.collectors.fred_oil.fetch_series_observations")
def test_collect_oil_prices_empty(
    mock_fetch: MagicMock,
    mock_write: MagicMock,
    mock_settings: None,
) -> None:
    from src.storage.writer import init_db

    init_db()
    mock_fetch.return_value = []

    from src.collectors.fred_oil import collect_oil_prices

    count = collect_oil_prices(start_date="2026-01-01", end_date="2026-01-31")
    assert count == 0
    mock_write.assert_not_called()
