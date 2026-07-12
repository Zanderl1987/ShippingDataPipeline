import requests
import pandas as pd
import time
from datetime import datetime, timedelta
from typing import Optional

from config import RAW_DIR
from storage.duckdb_storage import Storage


class MarineCadastreIngestor:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.base_url = "https://ais.uscg.mil/api"
        self.session = requests.Session()

    def get_usvrs(self, vessel_name: str = None, vessel_type: str = None) -> pd.DataFrame:
        params = {}
        if vessel_name:
            params["VesselName"] = vessel_name
        if vessel_type:
            params["VesselType"] = vessel_type

        resp = self.session.get(f"{self.base_url}/usvrs", params=params, timeout=30)
        resp.raise_for_status()
        return self._normalize_vessel(resp.json())

    def get_vessel_track(self, mmsi: str, start_date: str, end_date: str) -> pd.DataFrame:
        params = {
            "MMSI": mmsi,
            "startDate": start_date,
            "endDate": end_date,
        }
        resp = self.session.get(f"{self.base_url}/track", params=params, timeout=60)
        resp.raise_for_status()
        return self._normalize_track(resp.json())

    def get_vessel_list(self, port_ode: str = None) -> pd.DataFrame:
        params = {}
        if port_ode:
            params["PortODE"] = port_ode

        resp = self.session.get(f"{self.base_url}/vessels", params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return pd.DataFrame(data.get("vessels", []))

    def ingest_vessel_activity(
        self,
        port_lat: float,
        port_lon: float,
        radius_nm: float = 50.0,
        start_date: str = None,
        end_date: str = None,
    ) -> pd.DataFrame:
        if start_date is None:
            start_date = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        if end_date is None:
            end_date = datetime.utcnow().strftime("%Y-%m-%d")

        params = {
            "lat": port_lat,
            "lon": port_lon,
            "radius": radius_nm,
            "startDate": start_date,
            "endDate": end_date,
        }

        resp = self.session.get(f"{self.base_url}/vessels", params=params, timeout=60)
        resp.raise_for_status()
        return self._normalize_vessel(resp.json())

    def _normalize_vessel(self, data: dict) -> pd.DataFrame:
        rows = []
        for vessel in data.get("vessels", []):
            rows.append({
                "id": f"mc_{vessel.get('MMSI', '')}",
                "vessel_id": vessel.get("MMSI"),
                "vessel_name": vessel.get("VesselName"),
                "vessel_type": vessel.get("VesselType"),
                "imo_number": vessel.get("IMO"),
                "mmsi": vessel.get("MMSI"),
                "lat": vessel.get("lat"),
                "lon": vessel.get("lon"),
                "speed_knots": vessel.get("Speed"),
                "course": vessel.get("Course"),
                "heading": vessel.get("Heading"),
                "distance_from_shore_km": vessel.get("DistanceFromShore"),
                "distance_from_port_km": vessel.get("DistanceFromPort"),
                "port_id": vessel.get("Port"),
                "port_name": vessel.get("PortName"),
                "start_time": vessel.get("DateTime"),
                "end_time": vessel.get("DateTime"),
                "hours_present": 1.0,
                "source": "marine_cadastre",
                "source_country": "US",
                "ingested_at": datetime.utcnow().isoformat(),
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        self.storage.insert_dataframe("vessel_presence", df)
        return df

    def _normalize_track(self, data: dict) -> pd.DataFrame:
        rows = []
        for pos in data.get("track", []):
            rows.append({
                "id": f"mc_track_{pos.get('MMSI', '')}_{pos.get('DateTime', '')}",
                "vessel_id": pos.get("MMSI"),
                "vessel_name": pos.get("VesselName"),
                "vessel_type": pos.get("VesselType"),
                "imo_number": pos.get("IMO"),
                "mmsi": pos.get("MMSI"),
                "lat": pos.get("lat"),
                "lon": pos.get("lon"),
                "speed_knots": pos.get("Speed"),
                "course": pos.get("Course"),
                "heading": pos.get("Heading"),
                "distance_from_shore_km": pos.get("DistanceFromShore"),
                "distance_from_port_km": pos.get("DistanceFromPort"),
                "port_id": pos.get("Port"),
                "port_name": pos.get("PortName"),
                "start_time": pos.get("DateTime"),
                "end_time": pos.get("DateTime"),
                "hours_present": 1.0,
                "source": "marine_cadastre",
                "source_country": "US",
                "ingested_at": datetime.utcnow().isoformat(),
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        self.storage.insert_dataframe("vessel_presence", df)
        return df
