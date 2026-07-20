from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    data_dir: Path = Path("./data")
    storage_dir: Path = Path("./storage")
    log_level: str = "INFO"

    aisstream_api_key: str | None = None
    gfw_api_token: str | None = None
    vesselapi_api_key: str | None = None
    shiplookup_api_key: str | None = None
    un_comtrade_api_key: str | None = None

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            data_dir=Path(os.getenv("SDP_DATA_DIR", "./data")),
            storage_dir=Path(os.getenv("SDP_STORAGE_DIR", "./storage")),
            log_level=os.getenv("SDP_LOG_LEVEL", "INFO"),
            aisstream_api_key=os.getenv("AISSTREAM_API_KEY"),
            gfw_api_token=os.getenv("GFW_API_TOKEN"),
            vesselapi_api_key=os.getenv("VESSELAPI_API_KEY"),
            shiplookup_api_key=os.getenv("SHIPLOOKUP_API_KEY"),
            un_comtrade_api_key=os.getenv("UN_COMITRADE_API_KEY"),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "parquet").mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()
