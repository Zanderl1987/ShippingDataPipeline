import requests
import pandas as pd
import time
from datetime import datetime
from typing import Optional
from io import StringIO

from config import RAW_DIR
from storage.duckdb_storage import Storage


class USITCIngestor:
    def __init__(self, storage: Storage):
        self.storage = storage
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": "Mozilla/5.0"})

    def download_hts_file(self, year: int = 2024) -> str:
        url = f"https://hts.usitc.gov/view/{year}"
        resp = self.session.get(url, timeout=30)
        resp.raise_for_status()
        filepath = RAW_DIR / f"hts_{year}.html"
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text(resp.text, encoding="utf-8")
        return str(filepath)

    def ingest_hts_from_comtrade(self, chapter: str = "TOTAL") -> pd.DataFrame:
        url = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
        params = {
            "reporterCode": 842,
            "partnerCode": 156,
            "cmdCode": chapter,
            "period": "2023",
            "max": 5000,
            "includeDesc": True,
        }
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            records = data.get("data", [])
            if records:
                df = pd.DataFrame(records)
                return self._normalize_comtrade_hs(df)
        except Exception as e:
            print(f"Comtrade HS error: {e}")
        return pd.DataFrame()

    def ingest_all_chapters_from_comtrade(self) -> pd.DataFrame:
        all_dfs = []
        for ch in range(1, 99):
            code = f"{ch:02d}"
            try:
                df = self.ingest_hts_from_comtrade(code)
                if not df.empty:
                    all_dfs.append(df)
                    print(f"  Chapter {code}: {len(df)} rows")
            except Exception as e:
                print(f"  Chapter {code} error: {e}")
            time.sleep(0.5)

        if all_dfs:
            combined = pd.concat(all_dfs, ignore_index=True)
            self.storage.insert_dataframe("hs_reference", combined)
            return combined
        return pd.DataFrame()

    def search_hts(self, query: str) -> pd.DataFrame:
        url = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"
        params = {
            "reporterCode": 842,
            "partnerCode": 156,
            "cmdCode": "TOTAL",
            "period": "2023",
            "max": 5000,
            "includeDesc": True,
        }
        try:
            resp = self.session.get(url, params=params, timeout=30)
            resp.raise_for_status()
            data = resp.json()
            records = data.get("data", [])
            if records:
                df = pd.DataFrame(records)
                if query:
                    mask = df.apply(lambda row: row.astype(str).str.contains(query, case=False).any(), axis=1)
                    df = df[mask]
                return self._normalize_comtrade_hs(df)
        except Exception as e:
            print(f"Search error: {e}")
        return pd.DataFrame()

    def _normalize_comtrade_hs(self, df: pd.DataFrame) -> pd.DataFrame:
        rows = []
        for _, row in df.iterrows():
            code = str(row.get("cmdCode", ""))
            desc = str(row.get("cmdDesc", ""))
            rows.append({
                "hs_code": code,
                "hs_level": len(code) // 2,
                "description": desc,
                "parent_code": code[:2] if len(code) > 2 else "",
                "chapter": code[:2],
                "section": "",
            })
        if rows:
            result = pd.DataFrame(rows)
            self.storage.insert_dataframe("hs_reference", result)
            return result
        return pd.DataFrame()
