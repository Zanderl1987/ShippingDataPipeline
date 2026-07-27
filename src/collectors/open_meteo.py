"""Collect marine weather and forecasts from Open-Meteo."""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import polars as pl
import requests

from src.storage.tracker import SourceTracker, TimedCollector
from src.storage.writer import write_raw

logger = logging.getLogger(__name__)

MARINE_BASE_URL = "https://marine-api.open-meteo.com/v1/marine"
WEATHER_BASE_URL = "https://api.open-meteo.com/v1/forecast"

SOURCE = "open_meteo"

MARINE_VARIABLES = [
    "wave_height",
    "wave_direction",
    "wave_period",
    "swell_wave_height",
    "swell_wave_direction",
    "swell_wave_period",
    "ocean_current_velocity",
    "ocean_current_direction",
    "sea_surface_temperature",
]

WEATHER_VARIABLES = [
    "wind_speed_10m",
    "wind_direction_10m",
    "wind_gusts_10m",
    "pressure_msl",
    "temperature_2m",
    "precipitation",
]


def fetch_marine(
    latitude: float,
    longitude: float,
    *,
    past_days: int = 0,
    forecast_days: int = 5,
) -> dict[str, Any]:
    """Fetch marine weather forecast from Open-Meteo.

    Args:
        latitude: Latitude coordinate.
        longitude: Longitude coordinate.
        past_days: Number of past days to include (0-92).
        forecast_days: Number of forecast days (0-8).

    Returns:
        Raw JSON response dict.
    """
    params: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(MARINE_VARIABLES),
        "past_days": past_days,
        "forecast_days": forecast_days,
        "cell_selection": "sea",
    }

    logger.info("Fetching marine weather for (%s, %s)", latitude, longitude)
    resp = requests.get(MARINE_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def fetch_weather(
    latitude: float,
    longitude: float,
    *,
    past_days: int = 0,
    forecast_days: int = 5,
) -> dict[str, Any]:
    """Fetch weather forecast from Open-Meteo.

    Args:
        latitude: Latitude coordinate.
        longitude: Longitude coordinate.
        past_days: Number of past days to include (0-92).
        forecast_days: Number of forecast days (0-16).

    Returns:
        Raw JSON response dict.
    """
    params: dict[str, Any] = {
        "latitude": latitude,
        "longitude": longitude,
        "hourly": ",".join(WEATHER_VARIABLES),
        "past_days": past_days,
        "forecast_days": forecast_days,
    }

    logger.info("Fetching weather for (%s, %s)", latitude, longitude)
    resp = requests.get(WEATHER_BASE_URL, params=params, timeout=30)
    resp.raise_for_status()
    result: dict[str, Any] = resp.json()
    return result


def _parse_marine_response(
    data: dict[str, Any],
    latitude: float,
    longitude: float,
) -> pl.DataFrame:
    """Parse marine weather response into a Polars DataFrame."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    if not times:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for i, t in enumerate(times):
        record: dict[str, Any] = {
            "timestamp": t,
            "latitude": latitude,
            "longitude": longitude,
        }
        for var in MARINE_VARIABLES:
            values = hourly.get(var, [])
            record[var] = values[i] if i < len(values) else None
        records.append(record)

    df = pl.DataFrame(records)

    df = df.with_columns(
        pl.col("timestamp").str.to_datetime(strict=False).alias("timestamp")
    )

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def _parse_weather_response(
    data: dict[str, Any],
    latitude: float,
    longitude: float,
) -> pl.DataFrame:
    """Parse weather response into a Polars DataFrame."""
    hourly = data.get("hourly", {})
    times = hourly.get("time", [])

    if not times:
        return pl.DataFrame()

    records: list[dict[str, Any]] = []
    for i, t in enumerate(times):
        record: dict[str, Any] = {
            "timestamp": t,
            "latitude": latitude,
            "longitude": longitude,
        }
        for var in WEATHER_VARIABLES:
            values = hourly.get(var, [])
            record[var] = values[i] if i < len(values) else None
        records.append(record)

    df = pl.DataFrame(records)

    df = df.with_columns(
        pl.col("timestamp").str.to_datetime(strict=False).alias("timestamp")
    )

    today = date.today()
    df = df.with_columns(
        pl.lit(today).alias("partition_date"),
        pl.lit(SOURCE).alias("source"),
    )

    return df


def collect_marine(
    latitude: float,
    longitude: float,
    *,
    past_days: int = 0,
    forecast_days: int = 5,
    tracker: SourceTracker | None = None,
) -> int:
    """Fetch marine weather data and write to storage.

    Args:
        latitude: Latitude coordinate.
        longitude: Longitude coordinate.
        past_days: Number of past days to include.
        forecast_days: Number of forecast days.
        tracker: Optional SourceTracker for recording collection events.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = fetch_marine(
            latitude,
            longitude,
            past_days=past_days,
            forecast_days=forecast_days,
        )
        df = _parse_marine_response(raw, latitude, longitude)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No marine data returned for (%s, %s)", latitude, longitude)
            return 0

        logger.info("Writing %d marine records to storage", df.height)
        count = write_raw(SOURCE, df, table_name="marine_weather")
        tc.rows_written = count
        return count


def collect_weather(
    latitude: float,
    longitude: float,
    *,
    past_days: int = 0,
    forecast_days: int = 5,
    tracker: SourceTracker | None = None,
) -> int:
    """Fetch weather data and write to storage.

    Args:
        latitude: Latitude coordinate.
        longitude: Longitude coordinate.
        past_days: Number of past days to include.
        forecast_days: Number of forecast days.
        tracker: Optional SourceTracker for recording collection events.

    Returns:
        Number of rows written.
    """
    if tracker is None:
        tracker = SourceTracker()

    with TimedCollector(tracker, SOURCE) as tc:
        raw = fetch_weather(
            latitude,
            longitude,
            past_days=past_days,
            forecast_days=forecast_days,
        )
        df = _parse_weather_response(raw, latitude, longitude)
        tc.rows_fetched = df.height
        if df.height == 0:
            logger.warning("No weather data returned for (%s, %s)", latitude, longitude)
            return 0

        logger.info("Writing %d weather records to storage", df.height)
        count = write_raw(SOURCE, df, table_name="weather")
        tc.rows_written = count
        return count
