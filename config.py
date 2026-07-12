import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# --- Paths ---
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PARQUET_DIR = DATA_DIR / "parquet"

# --- API Keys ---
IMPORTYETI_API_KEY = os.getenv("IMPORTYETI_API_KEY", "")
COMTRADE_SUBSCRIPTION_KEY = os.getenv("COMTRADE_SUBSCRIPTION_KEY", "")
GFW_API_KEY = os.getenv("GFW_API_KEY", "")
ITC_USERNAME = os.getenv("ITC_USERNAME", "")
ITC_PASSWORD = os.getenv("ITC_PASSWORD", "")

# --- UN Comtrade ---
COMTRADE_API_URL = "https://comtradeapi.un.org/public/v1/preview/C/A/HS"

# --- ImportYeti ---
IMPORTYETI_API_URL = "https://data.importyeti.com/api/v1"

# --- Global Fishing Watch ---
GFW_API_URL = "https://gateway.globalfishingwatch.org/v3"
GFW_DATASET = "public-global-presence:latest"

# --- Rate Limits (requests per day) ---
COMTRADE_DAILY_LIMIT = 500
IMPORTYETI_DAILY_LIMIT = 100
GFW_DAILY_LIMIT = 1000
