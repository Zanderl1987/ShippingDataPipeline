# Shipping Data Pipeline

Collect, store, and analyze publicly available shipping data — vessel movements, port calls, freight rates, oil trade, and maritime risk.

## Quick Start

```bash
# Install dependencies
uv sync

# Run tests
uv run python -m pytest tests/ -q

# Lint and type check
uv run ruff check .
uv run mypy src/
```

## Installation

### Prerequisites

- Python 3.12+
- [uv](https://docs.astral.sh/uv/) package manager

### Setup

```bash
git clone https://github.com/Zanderl1987/ShippingDataPipeline.git
cd ShippingDataPipeline
uv sync
cp .env.example .env
# Edit .env with your API keys (optional — many sources work without auth)
```

## Configuration

API keys are configured via environment variables or `.env` file:

| Variable | Source | Required |
|----------|--------|----------|
| `AISSTREAM_API_KEY` | AISStream.io | No (free registration) |
| `GFW_API_TOKEN` | Global Fishing Watch | No (free registration) |
| `VESSELAPI_API_KEY` | VesselAPI | No (free tier) |
| `SHIPLOOKUP_API_KEY` | ShipLookup | No (free tier) |
| `UN_COMTRADE_API_KEY` | UN Comtrade | No (free registration) |
| `BARENTSWATCH_TOKEN` | BarentsWatch | No (free registration) |
| `EIA_API_KEY` | EIA Petroleum | No (free registration) |
| `HORMUZ_API_KEY` | Hormuz Monitor | No (free tier) |
| `OILPRICEAPI_API_KEY` | OilPriceAPI | No (free tier) |

**Sources that require no auth:** Axiomancer, Open-Meteo, Eagle Intelligence, IMF PortWatch, TankerMap, JODI-Oil, Digitraffic

## CLI Commands

```bash
sdp overview      # Pipeline overview — sources, row counts
sdp vessels       # Active vessels (with date/source filters)
sdp ports         # Port activity metrics
sdp congestion    # Port congestion estimates
sdp destinations  # Vessel destination summary
sdp routes        # Port-to-port route pairs
sdp weather       # Recent marine weather data
sdp status        # Pipeline health — staleness, null rates
sdp collect       # Run data collection manually
sdp quality       # Detailed quality metrics
```

### CLI Examples

```bash
# Show vessels from last 7 days
sdp vessels --date-from 2024-01-01 --date-to 2024-01-07

# Run collection for specific sources only
sdp collect --sources axiomancer open_meteo

# Force collection even if recently run
sdp collect --force

# Show only quality warnings
sdp status --warnings-only
```

## Data Sources (19 collectors)

### AIS Tracking (No Auth)
| Collector | Data | Schedule |
|-----------|------|----------|
| Axiomancer | Global AIS positions, tanker filter | Daily |
| Open-Meteo | Marine weather forecasts | Daily |
| Eagle Intelligence | Chokepoint risk scores (6 straits) | Daily |
| Digitraffic | Baltic Sea AIS positions + vessels + port calls (Finnish ports) | Daily / Weekly |

### AIS Tracking (Free Registration)
| Collector | Data | Auth |
|-----------|------|------|
| Global Fishing Watch | AIS + fishing events (2012+) | Bearer token |
| VesselAPI | Port events + vessel lookup | Bearer token |
| BarentsWatch | Norwegian waters AIS | Bearer token |
| AISStream | Real-time AIS WebSocket | API key |

### Trade & Registry (Free Registration)
| Collector | Data | Auth |
|-----------|------|------|
| UN Comtrade | Global trade flows | API key |
| ShipLookup | Vessel registry (1K credits/mo) | API key |
| NOAA MarineCadastre | US waters historical AIS | None |

### Oil & Energy (Free)
| Collector | Data | Auth |
|-----------|------|------|
| EIA Petroleum | US crude stocks, refinery, imports | API key |
| JODI-Oil | Global oil production/trade | None (CSV) |
| IMF PortWatch | Chokepoint transits + capacity | None (ArcGIS) |
| TankerMap | Live tanker positions, port calls | None |
| Hormuz Monitor | Risk scores, oil prices, VLCC rates | API key |
| OilPriceAPI | Oil price benchmarks + freight indices | API key |

## Project Structure

```
src/
├── config.py              # Environment-based configuration
├── collectors/            # 16 data source collectors
│   ├── axiomancer.py      # Global AIS (no auth)
│   ├── open_meteo.py      # Marine weather
│   ├── eagle_intelligence.py  # Chokepoint risk
│   ├── global_fishing_watch.py  # AIS + fishing events
│   ├── vesselapi.py       # Port events + vessels
│   ├── un_comtrade.py     # Global trade flows
│   ├── eia_petroleum.py   # US petroleum data
│   ├── jodi_oil.py        # Global oil trade
│   ├── imf_portwatch.py   # Chokepoint transits
│   ├── tankermap.py       # Tanker positions
│   ├── hormuz_monitor.py  # Risk + oil prices
│   ├── digitraffic.py     # Baltic AIS + vessels + port calls
│   ├── oilpriceapi.py     # Oil benchmarks + freight indices
│   └── ...
├── storage/               # DuckDB + Parquet storage
│   ├── schema.py          # 14 table definitions
│   ├── writer.py          # write_raw(), write_curated()
│   ├── reader.py          # query(), read_dataset()
│   └── tracker.py         # SourceTracker, TimedCollector
├── curation/              # Dedup, validation, enrichment
├── analytics/             # Routes, congestion, trade flow, reports
└── monitoring/            # Quality, notifications, collection orchestrator
    ├── quality.py         # Row counts, null rates, staleness
    ├── notify.py          # Slack/Discord, email, log file
    └── collect_all.py     # Run all collectors with scheduling
```

## Storage

- **DuckDB** — SQL query engine (`storage/pipeline.db`)
- **Parquet** — Columnar file storage (`storage/parquet/raw/{source}/`)
- **14 tables:** ais_positions, vessels, port_calls, ports, marine_weather, weather, trade_flow, freight_rates, chokepoint_status, oil_inventories, chokepoint_transits, oil_prices, oil_trade, source_tracking

### Querying Data

```python
from src.storage.reader import query, read_dataset

# SQL query
df = query("SELECT * FROM ais_positions WHERE source = ? LIMIT 100", ["axiomancer"])

# Read with filters
df = read_dataset(
    "port_calls",
    date_from=date(2024, 1, 1),
    date_to=date(2024, 1, 31),
    source="vesselapi",
    limit=1000,
)

# List all sources with row counts
from src.storage.reader import list_sources
sources = list_sources()
```

## GitHub Actions

The pipeline runs automatically via GitHub Actions:

- **Schedule:** Daily at 06:00 UTC
- **Manual trigger:** Actions → "Collect Data" → Run workflow
- **Steps:** Lint → Type check → Test → Collect → Quality gate → Artifacts

### Setup GitHub Secrets

Go to your repo → Settings → Secrets and variables → Actions → New repository secret:

| Secret Name | Value |
|-------------|-------|
| `AISSTREAM_API_KEY` | Your AISStream API key |
| `GFW_API_TOKEN` | Your GFW token |
| `EIA_API_KEY` | Your EIA API key |
| `HORMUZ_API_KEY` | Your Hormuz Monitor key |
| `OILPRICEAPI_API_KEY` | Your OilPriceAPI key |

Sources without auth (Axiomancer, Open-Meteo, Eagle Intelligence, PortWatch, TankerMap, JODI-Oil, Digitraffic) run without secrets.

## Notifications

Configure notifications in `.env`:

```bash
# Slack or Discord webhook
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
DISCORD_WEBHOOK_URL=https://discord.com/api/webhooks/...

# Email (optional)
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your@email.com
SMTP_PASSWORD=your-app-password
EMAIL_FROM=your@email.com
EMAIL_TO=alerts@example.com
```

## Testing

```bash
# Run all tests
uv run python -m pytest tests/ -q

# Run specific test module
uv run python -m pytest tests/collectors/test_axiomancer.py -v

# Run with coverage
uv run python -m pytest tests/ --cov=src --cov-report=term-missing
```

## Documentation

- [Project Plan](staging/PLAN.md)
- [Data Sources Catalog](staging/DATA_SOURCES.md)
- [Session Notes](staging/SESSION_NOTES.md)
- [Remaining Work](staging/REMAINING_WORK.md)

## License

Data sources are used under their respective licenses (CC BY 4.0 where applicable). Code in this repository is for research and analysis purposes.
