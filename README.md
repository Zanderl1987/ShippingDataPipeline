# Shipping & Trade Data Pipeline

Ingests shipping, import/export, and trade data from free public sources into a DuckDB-backed analytical database.

## Data Sources

| Source | Data | API Required |
|--------|------|--------------|
| ImportYeti | US Bill of Lading (shipment-level) | `IMPORTYETI_API_KEY` |
| UN Comtrade | Global bilateral trade flows (HS6) | `COMTRADE_SUBSCRIPTION_KEY` |
| Global Fishing Watch | AIS vessel presence/traffic | `GFW_API_KEY` |
| US ITC | HTS code reference (HTS10) | Free |
| Marine Cadastre | US vessel traffic | Free |
| Baltic Exchange | Freight rate indices | Free (web scrape) |
| WTO | Tariff rates + trade flows | Free |

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API keys
```

## Usage

```bash
# List available sources
python run_ingest.py --list-sources

# Ingest all sources
python run_ingest.py --all

# Ingest a specific source
python run_ingest.py --source usitc
python run_ingest.py --source freight

# Search ImportYeti
python run_ingest.py --source importyeti --query "Apple Inc"

# Get vessel presence at a port
python run_ingest.py --source gfw --query "33.7,-118.2"
```

## Storage

All data is stored in `shipping.duckdb` with the following tables:

- `bills_of_lading` — Shipment-level BoL data
- `trade_flows` — Bilateral trade flow data
- `vessel_presence` — AIS vessel position/presence data
- `freight_rates` — Shipping freight rate indices
- `tariff_rates` — Tariff rate data
- `hs_reference` — HS/HTS code reference table
- `port_reference` — Port location reference

## Project Structure

```
ShippingAnalysis/
├── config.py                    # API keys, paths, constants
├── run_ingest.py                # Main ingestion CLI
├── requirements.txt
├── ingestion/
│   ├── importyeti.py           # ImportYeti API
│   ├── comtrade.py             # UN Comtrade API
│   ├── global_fishing_watch.py # Global Fishing Watch API
│   ├── us_itc.py               # US ITC HTS codes
│   ├── marine_cadastre.py      # US vessel traffic
│   ├── freight_rates.py        # Freight rate indices
│   └── wto.py                  # WTO tariff data
├── storage/
│   └── duckdb_storage.py       # DuckDB storage layer
├── data/
│   ├── raw/                    # Raw downloaded files
│   └── parquet/                # Parquet exports
└── utils/                      # Shared utilities
```
