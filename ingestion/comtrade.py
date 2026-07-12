import requests
import pandas as pd
import time
from datetime import datetime
from typing import Optional

from config import COMTRADE_SUBSCRIPTION_KEY, COMTRADE_DAILY_LIMIT, COMTRADE_API_URL
from storage.duckdb_storage import Storage


class ComtradeIngestor:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.api_key = COMTRADE_SUBSCRIPTION_KEY
        self.base_url = COMTRADE_API_URL
        self.daily_limit = COMTRADE_DAILY_LIMIT
        self.requests_today = 0
        self.session = requests.Session()

    def _check_rate_limit(self):
        if self.requests_today >= self.daily_limit:
            raise RuntimeError(
                f"Comtrade daily limit ({self.daily_limit}) reached."
            )

    def _get(self, params: dict) -> dict:
        self._check_rate_limit()
        if self.api_key:
            params["subscription-key"] = self.api_key
        resp = self.session.get(self.base_url, params=params, timeout=30)
        resp.raise_for_status()
        self.requests_today += 1
        return resp.json()

    def ingest_trade_flow(
        self,
        reporter_code: int,
        partner_code: int,
        hs_code: str,
        period: str,
        flow_code: str = "M",
    ) -> pd.DataFrame:
        try:
            params = {
                "reporterCode": reporter_code,
                "partnerCode": partner_code,
                "cmdCode": hs_code,
                "period": period,
                "flowCode": flow_code,
                "max": 5000,
            }
            data = self._get(params)
            records = data.get("data", [])
            if records:
                df = pd.DataFrame(records)
                normalized = self._normalize(df)
                self.storage.insert_dataframe("trade_flows", normalized)
                return normalized
        except Exception as e:
            print(f"Comtrade error: {e}")
        return pd.DataFrame()

    def ingest_bilateral_flows(
        self,
        reporter_codes: list,
        partner_codes: list,
        period: str,
        hs_codes: list = None,
    ) -> pd.DataFrame:
        if hs_codes is None:
            hs_codes = ["TOTAL"]

        all_dfs = []
        for reporter in reporter_codes:
            for partner in partner_codes:
                if reporter == partner:
                    continue
                try:
                    params = {
                        "reporterCode": reporter,
                        "partnerCode": partner,
                        "cmdCode": ",".join(hs_codes),
                        "period": period,
                        "max": 5000,
                    }
                    data = self._get(params)
                    records = data.get("data", [])
                    if records:
                        df = pd.DataFrame(records)
                        normalized = self._normalize(df)
                        all_dfs.append(normalized)
                except Exception as e:
                    print(f"Error fetching {reporter}->{partner}: {e}")
                time.sleep(0.5)

        if all_dfs:
            combined = pd.concat(all_dfs, ignore_index=True)
            self.storage.insert_dataframe("trade_flows", combined)
            return combined
        return pd.DataFrame()

    def ingest_top_traders(
        self, period: str, flow_code: str = "M", top_n: int = 50
    ) -> pd.DataFrame:
        try:
            params = {
                "reporterCode": "all",
                "cmdCode": "TOTAL",
                "period": period,
                "flowCode": flow_code,
                "max": top_n,
                "includeDesc": True,
            }
            data = self._get(params)
            records = data.get("data", [])
            if records:
                df = pd.DataFrame(records)
                normalized = self._normalize(df)
                self.storage.insert_dataframe("trade_flows", normalized)
                return normalized
        except Exception as e:
            print(f"Error fetching top traders: {e}")
        return pd.DataFrame()

    def ingest_recent_month(
        self, reporter_code: int, partner_code: int = None, hs_code: str = "TOTAL"
    ) -> pd.DataFrame:
        now = datetime.utcnow()
        period = f"{now.year}{now.month:02d}"

        params = {
            "reporterCode": reporter_code,
            "cmdCode": hs_code,
            "period": period,
            "max": 5000,
            "includeDesc": True,
        }
        if partner_code:
            params["partnerCode"] = partner_code

        try:
            data = self._get(params)
            records = data.get("data", [])
            if records:
                df = pd.DataFrame(records)
                normalized = self._normalize(df)
                self.storage.insert_dataframe("trade_flows", normalized)
                return normalized
        except Exception as e:
            print(f"Comtrade error: {e}")
        return pd.DataFrame()

    def _normalize(self, df: pd.DataFrame) -> pd.DataFrame:
        rename_map = {
            "period": "period",
            "reporterCode": "reporter_code",
            "reporterDesc": "reporter_name",
            "flowCode": "flow_code",
            "flowDesc": "flow_desc",
            "partnerCode": "partner_code",
            "partnerDesc": "partner_name",
            "cmdCode": "commodity_code",
            "cmdDesc": "commodity_desc",
            "primaryValue": "primary_value",
            "netWgt": "net_weight_kg",
            "grossWgt": "gross_weight_kg",
        }
        df = df.rename(columns={k: v for k, v in rename_map.items() if k in df.columns})
        df["trade_type"] = "bilateral"
        df["source"] = "un_comtrade"
        df["ingested_at"] = datetime.utcnow().isoformat()
        return df

    def get_hs_codes_for_chapter(self, chapter: str, reporter: int = 156, partner: int = 842) -> pd.DataFrame:
        try:
            params = {
                "reporterCode": reporter,
                "partnerCode": partner,
                "cmdCode": f"{chapter}**",
                "period": "2023",
                "max": 5000,
                "includeDesc": True,
            }
            data = self._get(params)
            records = data.get("data", [])
            if records:
                return pd.DataFrame(records)
        except Exception as e:
            print(f"Error: {e}")
        return pd.DataFrame()
