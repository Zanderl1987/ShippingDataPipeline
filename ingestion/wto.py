import requests
import pandas as pd
import time
from datetime import datetime
from typing import Optional

from config import RAW_DIR
from storage.duckdb_storage import Storage


class WTOIngestor:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.session = requests.Session()
        self.base_url = "https://www.wto.org/english/res_e/statistics_e/statistics_e.htm"

    def get_tariff_data(self, reporter_code: int, product_code: str) -> pd.DataFrame:
        url = f"https://tao.wto.org/api/tariff/{reporter_code}/{product_code}"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return self._normalize_tariff(resp.json(), reporter_code, product_code)
        except Exception as e:
            print(f"WTO tariff error: {e}")
            return pd.DataFrame()

    def get_trade_profile(self, reporter_code: int) -> pd.DataFrame:
        url = f"https://tao.wto.org/api/trade-profile/{reporter_code}"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return self._normalize_profile(resp.json(), reporter_code)
        except Exception as e:
            print(f"WTO profile error: {e}")
            return pd.DataFrame()

    def get_top_traders(self, year: str = "2023") -> pd.DataFrame:
        url = f"https://www.wto.org/english/res_e/statis_e/statis_e.htm"
        headers = {"User-Agent": "Mozilla/5.0"}
        try:
            resp = self.session.get(url, headers=headers, timeout=30)
            resp.raise_for_status()
            return pd.DataFrame()
        except Exception as e:
            print(f"WTO top traders error: {e}")
            return pd.DataFrame()

    def ingest_trade_profile(self, reporter_code: int) -> pd.DataFrame:
        df = self.get_trade_profile(reporter_code)
        if not df.empty:
            self.storage.insert_dataframe("trade_flows", df)
        return df

    def ingest_top_traders(self, top_n: int = 50) -> pd.DataFrame:
        country_codes = [
            156, 842, 276, 392, 826, 250, 380, 124, 0, 76,
            410, 792, 528, 756, 158, 702, 360, 458, 484, 643,
        ]

        all_dfs = []
        for code in country_codes[:top_n]:
            try:
                df = self.get_trade_profile(code)
                if not df.empty:
                    all_dfs.append(df)
            except Exception as e:
                print(f"Error {code}: {e}")
            time.sleep(0.5)

        if all_dfs:
            combined = pd.concat(all_dfs, ignore_index=True)
            self.storage.insert_dataframe("trade_flows", combined)
            return combined
        return pd.DataFrame()

    def _normalize_tariff(self, data: dict, reporter_code: int, product_code: str) -> pd.DataFrame:
        rows = []
        for item in data.get("tariffs", []):
            rows.append({
                "id": f"wto_tariff_{reporter_code}_{product_code}_{item.get('partner', '')}",
                "reporter_code": reporter_code,
                "reporter_name": data.get("reporter_name"),
                "product_code": product_code,
                "product_desc": item.get("product_desc"),
                "partner_code": item.get("partner"),
                "partner_name": item.get("partner_name"),
                "ad_valorem_rate": item.get("ad_valorem_rate"),
                "tariff_type": item.get("tariff_type"),
                "period": item.get("period"),
                "source": "wto_ttd",
                "ingested_at": datetime.utcnow().isoformat(),
            })
        return pd.DataFrame(rows)

    def _normalize_profile(self, data: dict, reporter_code: int) -> pd.DataFrame:
        rows = []
        for flow in data.get("trade_flows", []):
            rows.append({
                "id": f"wto_flow_{reporter_code}_{flow.get('partner_code', '')}_{flow.get('commodity_code', '')}",
                "period": flow.get("year"),
                "reporter_code": reporter_code,
                "reporter_name": data.get("reporter_name"),
                "flow_code": flow.get("flow_code"),
                "flow_desc": flow.get("flow_desc"),
                "partner_code": flow.get("partner_code"),
                "partner_name": flow.get("partner_name"),
                "commodity_code": flow.get("commodity_code"),
                "commodity_desc": flow.get("commodity_desc"),
                "primary_value": flow.get("value"),
                "net_weight_kg": flow.get("net_weight"),
                "gross_weight_kg": flow.get("gross_weight"),
                "trade_type": "bilateral",
                "source": "wto_ttd",
                "ingested_at": datetime.utcnow().isoformat(),
            })
        return pd.DataFrame(rows)
