import requests
import pandas as pd
import time
import re
import json
from datetime import datetime
from typing import Optional

from config import RAW_DIR
from storage.duckdb_storage import Storage


class BalticExchangeIngestor:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def get_freight_from_trading_economics(self) -> pd.DataFrame:
        indices = [
            ("baltic", "BDI", "Baltic Dry Index"),
            ("baltic-capesize", "BCI", "Baltic Capesize Index"),
            ("baltic-panamax", "BPI", "Baltic Panamax Index"),
            ("baltic-supramax", "BSI", "Baltic Supramax Index"),
            ("containerized-freight-index", "CFI", "Containerized Freight Index"),
        ]

        all_dfs = []
        for slug, code, name in indices:
            try:
                url = f"https://tradingeconomics.com/commodity/{slug}"
                resp = self.session.get(url, timeout=30)
                df = self._parse_te_page(resp.text, code, name)
                all_dfs.append(df)
                print(f"  Fetched {name}: {len(df)} rows")
            except Exception as e:
                print(f"  Error fetching {name}: {e}")
            time.sleep(1)

        if all_dfs:
            combined = pd.concat(all_dfs, ignore_index=True)
            self.storage.insert_dataframe("freight_rates", combined)
            return combined
        return pd.DataFrame()

    def ingest_all_indices(self) -> pd.DataFrame:
        return self.get_freight_from_trading_economics()

    def get_container_freight_index(self) -> pd.DataFrame:
        return self.get_freight_from_trading_economics()

    def _parse_te_page(self, html: str, index_code: str, index_name: str) -> pd.DataFrame:
        rows = []

        json_match = re.search(r"var defined_values\s*=\s*({.*?});", html, re.DOTALL)
        if json_match:
            try:
                data = json.loads(json_match.group(1))
                for item in data.get("data", []):
                    rows.append({
                        "id": f"freight_{index_code}_{item.get('x', '')}",
                        "date": item.get("x"),
                        "index_name": index_name,
                        "index_value": item.get("y"),
                        "route": None,
                        "vessel_class": index_code,
                        "period_desc": None,
                        "source": "trading_economics",
                        "ingested_at": datetime.utcnow().isoformat(),
                    })
            except (json.JSONDecodeError, KeyError):
                pass

        if not rows:
            value_match = re.search(
                r'<span[^>]*id="p"[^>]*>([\d,.]+)</span>', html
            )
            if value_match:
                current_value = float(value_match.group(1).replace(",", ""))
                rows.append({
                    "id": f"freight_{index_code}_{datetime.utcnow().strftime('%Y-%m-%d')}",
                    "date": datetime.utcnow().strftime("%Y-%m-%d"),
                    "index_name": index_name,
                    "index_value": current_value,
                    "route": None,
                    "vessel_class": index_code,
                    "period_desc": None,
                    "source": "trading_economics",
                    "ingested_at": datetime.utcnow().isoformat(),
                })

        if not rows:
            data_match = re.search(r'"data"\s*:\s*(\[.*?\])', html, re.DOTALL)
            if data_match:
                try:
                    data_points = json.loads(data_match.group(1))
                    for item in data_points:
                        rows.append({
                            "id": f"freight_{index_code}_{item.get('x', '')}",
                            "date": item.get("x"),
                            "index_name": index_name,
                            "index_value": item.get("y"),
                            "route": None,
                            "vessel_class": index_code,
                            "period_desc": None,
                            "source": "trading_economics",
                            "ingested_at": datetime.utcnow().isoformat(),
                        })
                except (json.JSONDecodeError, KeyError):
                    pass

        return pd.DataFrame(rows)
