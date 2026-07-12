import requests
import pandas as pd
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import json

from config import (
    GFW_API_KEY,
    GFW_API_URL,
    GFW_DATASET,
    GFW_DAILY_LIMIT,
    RAW_DIR,
)
from storage.duckdb_storage import Storage


class GFWIngestor:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.api_key = GFW_API_KEY
        self.base_url = GFW_API_URL
        self.dataset = GFW_DATASET
        self.daily_limit = GFW_DAILY_LIMIT
        self.requests_today = 0
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _check_rate_limit(self):
        if self.requests_today >= self.daily_limit:
            raise RuntimeError(
                f"GFW daily limit ({self.daily_limit}) reached."
            )

    def _get(self, endpoint: str, params: dict = None) -> dict:
        self._check_rate_limit()
        url = f"{self.base_url}{endpoint}"
        resp = requests.get(url, headers=self.headers, params=params, timeout=30)
        resp.raise_for_status()
        self.requests_today += 1
        return resp.json()

    def get_vessel_presence(
        self,
        lat: float,
        lon: float,
        radius_km: float = 50.0,
        start_date: str = None,
        end_date: str = None,
    ) -> pd.DataFrame:
        if start_date is None:
            start_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        if end_date is None:
            end_date = datetime.utcnow().strftime("%Y-%m-%d")

        payload = {
            "dataset": self.dataset,
            "geometry": {
                "type": "Point",
                "coordinates": [lon, lat],
            },
            "radius": radius_km * 1000,
            "start_date": start_date,
            "end_date": end_date,
            "limit": 1000,
        }

        data = self._get("/vessels/presence", payload)
        return self._normalize_presence(data)

    def get_vessels_at_port(
        self,
        port_lat: float,
        port_lon: float,
        start_date: str = None,
        end_date: str = None,
    ) -> pd.DataFrame:
        if start_date is None:
            start_date = (datetime.utcnow() - timedelta(days=30)).strftime("%Y-%m-%d")
        if end_date is None:
            end_date = datetime.utcnow().strftime("%Y-%m-%d")

        payload = {
            "dataset": self.dataset,
            "geometry": {
                "type": "Point",
                "coordinates": [port_lon, port_lat],
            },
            "radius": 10000,
            "start_date": start_date,
            "end_date": end_date,
            "limit": 5000,
        }

        data = self._get("/vessels/presence", payload)
        return self._normalize_presence(data)

    def get_vessel_track(
        self,
        vessel_id: str,
        start_date: str,
        end_date: str,
    ) -> pd.DataFrame:
        payload = {
            "dataset": self.dataset,
            "vessel_id": vessel_id,
            "start_date": start_date,
            "end_date": end_date,
        }

        data = self._get("/vessels/tracks", payload)
        return self._normalize_track(data)

    def get_all_vessels(self, start_date: str = None, end_date: str = None) -> pd.DataFrame:
        if start_date is None:
            start_date = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        if end_date is None:
            end_date = datetime.utcnow().strftime("%Y-%m-%d")

        payload = {
            "dataset": self.dataset,
            "start_date": start_date,
            "end_date": end_date,
            "limit": 10000,
        }

        data = self._get("/vessels", payload)
        return self._normalize_vessels(data)

    def _normalize_presence(self, data: dict) -> pd.DataFrame:
        rows = []
        for entry in data.get("vessels", []):
            for pos in entry.get("positions", []):
                rows.append({
                    "id": f"gfw_{entry.get('vessel_id', '')}_{pos.get('timestamp', '')}",
                    "vessel_id": entry.get("vessel_id"),
                    "vessel_name": entry.get("vessel_name"),
                    "vessel_type": entry.get("vessel_type"),
                    "imo_number": entry.get("imo_number"),
                    "mmsi": entry.get("mmsi"),
                    "lat": pos.get("lat"),
                    "lon": pos.get("lon"),
                    "speed_knots": pos.get("speed"),
                    "course": pos.get("course"),
                    "heading": pos.get("heading"),
                    "distance_from_shore_km": pos.get("distance_from_shore"),
                    "distance_from_port_km": pos.get("distance_from_port"),
                    "port_id": pos.get("port_id"),
                    "port_name": pos.get("port_name"),
                    "start_time": pos.get("timestamp"),
                    "end_time": pos.get("timestamp"),
                    "hours_present": 1.0,
                    "source": "global_fishing_watch",
                    "source_country": "GLOBAL",
                    "ingested_at": datetime.utcnow().isoformat(),
                })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        self.storage.insert_dataframe("vessel_presence", df)
        return df

    def _normalize_track(self, data: dict) -> pd.DataFrame:
        rows = []
        for track in data.get("tracks", []):
            for pos in track.get("positions", []):
                rows.append({
                    "id": f"gfw_track_{track.get('vessel_id', '')}_{pos.get('timestamp', '')}",
                    "vessel_id": track.get("vessel_id"),
                    "vessel_name": track.get("vessel_name"),
                    "vessel_type": track.get("vessel_type"),
                    "imo_number": track.get("imo_number"),
                    "mmsi": track.get("mmsi"),
                    "lat": pos.get("lat"),
                    "lon": pos.get("lon"),
                    "speed_knots": pos.get("speed"),
                    "course": pos.get("course"),
                    "heading": pos.get("heading"),
                    "distance_from_shore_km": pos.get("distance_from_shore"),
                    "distance_from_port_km": pos.get("distance_from_port"),
                    "port_id": pos.get("port_id"),
                    "port_name": pos.get("port_name"),
                    "start_time": pos.get("timestamp"),
                    "end_time": pos.get("timestamp"),
                    "hours_present": 1.0,
                    "source": "global_fishing_watch",
                    "source_country": "GLOBAL",
                    "ingested_at": datetime.utcnow().isoformat(),
                })
        return pd.DataFrame(rows)

    def _normalize_vessels(self, data: dict) -> pd.DataFrame:
        rows = []
        for v in data.get("vessels", []):
            rows.append({
                "id": f"gfw_vessel_{v.get('vessel_id', '')}",
                "vessel_id": v.get("vessel_id"),
                "vessel_name": v.get("vessel_name"),
                "vessel_type": v.get("vessel_type"),
                "imo_number": v.get("imo_number"),
                "mmsi": v.get("mmsi"),
                "lat": v.get("lat"),
                "lon": v.get("lon"),
                "speed_knots": v.get("speed"),
                "course": v.get("course"),
                "heading": v.get("heading"),
                "distance_from_shore_km": v.get("distance_from_shore"),
                "distance_from_port_km": v.get("distance_from_port"),
                "port_id": v.get("port_id"),
                "port_name": v.get("port_name"),
                "start_time": v.get("last_position"),
                "end_time": v.get("last_position"),
                "hours_present": None,
                "source": "global_fishing_watch",
                "source_country": "GLOBAL",
                "ingested_at": datetime.utcnow().isoformat(),
            })
        return pd.DataFrame(rows)

    def ingest_port_region(
        self,
        port_lat: float,
        port_lon: float,
        port_name: str,
        months_back: int = 3,
    ) -> pd.DataFrame:
        end_date = datetime.utcnow().strftime("%Y-%m-%d")
        start_date = (datetime.utcnow() - timedelta(days=30 * months_back)).strftime("%Y-%m-%d")

        all_dfs = []
        current_start = datetime.strptime(start_date, "%Y-%m-%d")
        current_end = datetime.strptime(end_date, "%Y-%m-%d")

        while current_start < current_end:
            chunk_end = min(current_start + timedelta(days=30), current_end)
            df = self.get_vessels_at_port(
                port_lat,
                port_lon,
                start_date=current_start.strftime("%Y-%m-%d"),
                end_date=chunk_end.strftime("%Y-%m-%d"),
            )
            if not df.empty:
                all_dfs.append(df)
            current_start = chunk_end + timedelta(days=1)
            time.sleep(1)

        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
        return pd.DataFrame()
