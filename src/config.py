"""Environment-based configuration for the shipping pipeline."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _safe_int(val: str | None, default: int) -> int:
    """Parse an integer from a string, returning default on failure."""
    if val is None:
        return default
    try:
        return int(val)
    except (ValueError, TypeError):
        return default


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
    barentswatch_token: str | None = None
    eia_api_key: str | None = None
    hormuz_api_key: str | None = None
    oilpriceapi_api_key: str | None = None

    slack_webhook_url: str | None = None
    discord_webhook_url: str | None = None
    smtp_host: str | None = None
    smtp_port: int | None = None
    smtp_user: str | None = None
    smtp_password: str | None = None
    email_from: str | None = None
    email_to: str | None = None
    notification_log_file: str | None = None

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
            un_comtrade_api_key=os.getenv("UN_COMTRADE_API_KEY"),
            barentswatch_token=os.getenv("BARENTSWATCH_TOKEN"),
            eia_api_key=os.getenv("EIA_API_KEY"),
            hormuz_api_key=os.getenv("HORMUZ_API_KEY"),
            oilpriceapi_api_key=os.getenv("OILPRICEAPI_API_KEY"),
            slack_webhook_url=os.getenv("SLACK_WEBHOOK_URL"),
            discord_webhook_url=os.getenv("DISCORD_WEBHOOK_URL"),
            smtp_host=os.getenv("SMTP_HOST"),
            smtp_port=_safe_int(os.getenv("SMTP_PORT"), 587) if os.getenv("SMTP_PORT") else None,
            smtp_user=os.getenv("SMTP_USER"),
            smtp_password=os.getenv("SMTP_PASSWORD"),
            email_from=os.getenv("EMAIL_FROM"),
            email_to=os.getenv("EMAIL_TO"),
            notification_log_file=os.getenv("NOTIFICATION_LOG_FILE", "./data/notifications.log"),
        )

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.storage_dir.mkdir(parents=True, exist_ok=True)
        (self.storage_dir / "parquet").mkdir(parents=True, exist_ok=True)


settings = Settings.from_env()
