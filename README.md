# Shipping Data Pipeline

Collect, store, and analyze publicly available shipping data — vessel movements, port calls, freight rates, and trade flows.

## Quick Start

```bash
uv sync
uv run pytest
```

## Documentation

- [Project Plan](staging/PLAN.md)
- [Data Sources Catalog](staging/DATA_SOURCES.md)

## Project Structure

```
src/
├── config.py          # Environment-based configuration
├── collectors/        # Data source collectors
├── storage/           # DuckDB + Parquet storage layer
├── curation/          # Dedup, validation, enrichment
└── analytics/         # Analysis and reporting
```
