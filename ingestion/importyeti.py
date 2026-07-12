import requests
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
import pandas as pd

from config import (
    IMPORTYETI_API_KEY,
    IMPORTYETI_API_URL,
    IMPORTYETI_DAILY_LIMIT,
    RAW_DIR,
)
from storage.duckdb_storage import Storage


class ImportYetiIngestor:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.api_key = IMPORTYETI_API_KEY
        self.base_url = IMPORTYETI_API_URL
        self.headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        self.daily_limit = IMPORTYETI_DAILY_LIMIT
        self.requests_today = 0

    def _check_rate_limit(self):
        if self.requests_today >= self.daily_limit:
            raise RuntimeError(
                f"ImportYeti daily limit ({self.daily_limit}) reached. Try again tomorrow."
            )

    def _get(self, endpoint: str, params: dict = None) -> dict:
        self._check_rate_limit()
        url = f"{self.base_url}{endpoint}"
        resp = requests.get(url, headers=self.headers, params=params, timeout=30)
        resp.raise_for_status()
        self.requests_today += 1
        return resp.json()

    def search_company(self, company_name: str) -> dict:
        return self._get("/search/company", {"q": company_name, "page": 1})

    def get_company_shipments(
        self, company_id: str, page: int = 1, per_page: int = 50
    ) -> dict:
        return self._get(
            f"/company/{company_id}/shipments",
            {"page": page, "per_page": per_page},
        )

    def get_shipment_detail(self, shipment_id: str) -> dict:
        return self._get(f"/shipment/{shipment_id}")

    def search_port(self, port_name: str) -> dict:
        return self._get("/search/port", {"q": port_name})

    def search_vessel(self, vessel_name: str) -> dict:
        return self._get("/search/vessel", {"q": vessel_name})

    def ingest_company_shipments(
        self, company_id: str, max_pages: int = 10
    ) -> pd.DataFrame:
        all_rows = []
        for page in range(1, max_pages + 1):
            data = self.get_company_shipments(company_id, page=page)
            shipments = data.get("shipments", [])
            if not shipments:
                break
            for s in shipments:
                row = self._normalize_shipment(s, source="importyeti")
                all_rows.append(row)
        df = pd.DataFrame(all_rows)
        if not df.empty:
            self.storage.insert_dataframe("bills_of_lading", df)
        return df

    def _normalize_shipment(self, s: dict, source: str = "importyeti") -> dict:
        return {
            "id": f"iy_{s.get('id', '')}",
            "shipper_name": s.get("shipper", {}).get("name"),
            "shipper_address": s.get("shipper", {}).get("address"),
            "consignee_name": s.get("consignee", {}).get("name"),
            "consignee_address": s.get("consignee", {}).get("address"),
            "notify_party": s.get("notify_party"),
            "carrier": s.get("carrier"),
            "vessel_name": s.get("vessel_name"),
            "voyage_number": s.get("voyage_number"),
            "port_of_loading": s.get("port_of_loading"),
            "port_of_discharge": s.get("port_of_discharge"),
            "place_of_receipt": s.get("place_of_receipt"),
            "place_of_delivery": s.get("place_of_delivery"),
            "vessel_country": s.get("vessel_country"),
            "vessel_type": s.get("vessel_type"),
            "vessel_gross_tonnage": s.get("vessel_gross_tonnage"),
            "vessel_year_built": s.get("vessel_year_built"),
            "vessel_imo": s.get("vessel_imo"),
            "container_number": s.get("container_number"),
            "container_size": s.get("container_size"),
            "container_type": s.get("container_type"),
            "container_teu": s.get("container_teu"),
            "seal_number": s.get("seal_number"),
            "commodity_description": s.get("commodity_description"),
            "hs_code": s.get("hs_code"),
            "weight_kg": s.get("weight_kg"),
            "weight_lbs": s.get("weight_lbs"),
            "volume_cbm": s.get("volume_cbm"),
            "packages": s.get("packages"),
            "package_type": s.get("package_type"),
            "freight_charges": s.get("freight_charges"),
            "freight_currency": s.get("freight_currency"),
            "insurance_charges": s.get("insurance_charges"),
            "insurance_currency": s.get("insurance_currency"),
            "total_charges": s.get("total_charges"),
            "total_currency": s.get("total_currency"),
            "etd": s.get("etd"),
            "eta": s.get("eta"),
            "departure_date": s.get("departure_date"),
            "arrival_date": s.get("arrival_date"),
            "customs_status": s.get("customs_status"),
            "bill_of_lading_number": s.get("bill_of_lading_number"),
            "booking_number": s.get("booking_number"),
            "source": source,
            "source_country": "US",
        }

    def get_top_companies(self, page: int = 1) -> dict:
        return self._get("/company/top", {"page": page})

    def ingest_top_companies(self, max_pages: int = 5) -> pd.DataFrame:
        all_dfs = []
        for page in range(1, max_pages + 1):
            data = self.get_top_companies(page)
            companies = data.get("companies", [])
            if not companies:
                break
            for c in companies:
                cid = c.get("id")
                if cid:
                    df = self.ingest_company_shipments(cid, max_pages=2)
                    all_dfs.append(df)
        if all_dfs:
            return pd.concat(all_dfs, ignore_index=True)
        return pd.DataFrame()
