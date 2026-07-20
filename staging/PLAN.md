# Shipping Data Pipeline — Project Plan

## Vision
A modular, automated data pipeline that collects publicly available shipping data, stores it efficiently, and produces actionable analytics on vessel movements, port congestion, and trade flows.

---

## Tech Stack

| Layer | Choice | Why |
|-------|--------|-----|
| Language | Python 3.11+ | Ecosystem, existing patterns from financial-data-pipeline |
| Storage | DuckDB + Parquet | Columnar, embeddable, cheap, excellent for time-series |
| Orchestration | GitHub Actions / Task Scheduler | Minimal infrastructure; cron-style scheduling |
| Packaging | `uv` or `pip` + `requirements.txt` | Lightweight, reproducible |
| Testing | `pytest` | Standard, familiar |
| Quality | `ruff` + `mypy` | Keep code clean |

---

## Data Sources

Full vetted catalog with ToS, rate limits, auth model, and backfill depth for each source:
→ **[`staging/DATA_SOURCES.md`](DATA_SOURCES.md)**

### Phase 1 Priority Sources

| # | Source | Why First |
|---|--------|-----------|
| 1 | **Axiomancer Overwatch** — Free, no-auth AIS positions | Zero setup; instant dev feedback |
| 2 | **OpenAIS** — Free, no-auth, **historical from 2021** | Track reconstruction without waiting |
| 3 | **Seafarer Index** — Free, CC BY 4.0 vessel + port registry | Enrichment layer for all other sources |
| 4 | **Open-Meteo** — Free, no-auth weather/wave data | Ancillary for route/delay analysis |

### Phase 2 (Registration-based free tiers)

| # | Source | Why Here |
|---|--------|----------|
| 5 | **Global Fishing Watch** — Free token, 2012+ AIS + events | Rich historical data, event detection |
| 6 | **VesselAPI** — Free tier, port events + vessel lookup | Port call + vessel in one API |
| 7 | **UN Comtrade** — Free API key, global trade flows | Trade volume analysis |
| 8 | **ShipLookup API** — Free 1K credits/month | Vessel registry lookup |

---

## Pipeline Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│   COLLECTORS    │ ──> │    STORAGE      │ ──> │   ANALYTICS     │
│                 │     │                 │     │                 │
│  ais_collector  │     │  /raw/*.parquet │     │  route_mapper   │
│  port_collector │     │  /curated/*.pq  │     │  congestion     │
│  freight_collect │     │  /views/*.pq    │     │  trade_flow     │
│                 │     │  duckdb/db      │     │  reporting      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
```

### Data Flow

1. **Collect** — Each collector is an idempotent script that fetches from a source, normalizes, and writes raw Parquet partitioned by `collection_date` and `source`.
2. **Curate** — A curation layer deduplicates, validates, and enriches raw data into clean, query-ready tables.
3. **Analyze** — Analytics modules read from curated tables and produce derived datasets (routes, congestion metrics, trends).
4. **Serve** — Results can be viewed via CLI reports, Jupyter notebooks, or a lightweight dashboard.

### Key Design Principles

- **Idempotent** — Rerunning a collector produces the same result (upsert logic).
- **Incremental** — Only fetch new data since last run; store timestamps per source.
- **Partitioned** — All Parquet data partitioned by date for efficient reads.
- **Vetted** — Every source goes through a pre-build checklist (ToS, rate limits, backfill depth).
- **Tested** — Each collector and analysis module has dedicated unit + integration tests.

---

## Folder Structure

```
ShippingDataPipeline/
├── staging/                  # Planning & reference docs (this folder)
├── src/
│   ├── __init__.py
│   ├── config.py             # Central config (API keys, paths, schedules)
│   ├── collectors/           # One module per data source
│   │   ├── __init__.py
│   │   ├── ais.py
│   │   ├── ports.py
│   │   ├── freight.py
│   │   └── registry.py
│   ├── storage/              # DuckDB schema, Parquet IO, partitioning
│   │   ├── __init__.py
│   │   ├── schema.py
│   │   ├── reader.py
│   │   └── writer.py
│   ├── curation/             # Dedup, validation, enrichment
│   │   ├── __init__.py
│   │   ├── dedup.py
│   │   ├── validate.py
│   │   └── enrich.py
│   └── analytics/            # Analysis & reporting modules
│       ├── __init__.py
│       ├── routes.py
│       ├── congestion.py
│       ├── trade_flow.py
│       └── reports.py
├── tests/
│   ├── collectors/
│   ├── curation/
│   └── analytics/
├── notebooks/                # Exploration & analysis notebooks
├── data/                     # Local data (gitignored)
├── storage/                  # DuckDB database & partitioned Parquet
├── .env.example
├── .gitignore
├── Makefile (or tasks.ps1)
├── pyproject.toml
├── requirements.txt
└── README.md
```

---

## Phased Build Plan

### Phase 1 — Foundation (this session)
- [ ] Set up project scaffold (folders, config, `.gitignore`, `pyproject.toml`)
- [ ] Implement core storage layer (DuckDB schema, Parquet writer/reader)
- [ ] Implement `config.py` with env-based settings
- [ ] Write initial tests for storage layer
- [ ] Set up `ruff`/`mypy`/`pytest` toolchain

### Phase 2 — Data Sources
- [ ] Vet & integrate first data source (likely AIS or port data)
- [ ] Build collector with incremental fetch
- [ ] Add source tracking (checkpoints, timestamps)
- [ ] Write curation layer (dedup + validation)
- [ ] Integration tests for collector-to-storage flow

### Phase 3 — Analytics
- [ ] Build route mapping from AIS position sequences
- [ ] Build port congestion metrics (wait times, throughput)
- [ ] Build trade flow summaries (port-to-port volumes)
- [ ] Add CLI reporting (`python -m src report`)
- [ ] Unit tests for each analytic

### Phase 4 — Automation & Polish
- [ ] GitHub Actions workflow for scheduled collection
- [ ] Notification on failures / data gaps
- [ ] Optional: lightweight dashboard (Streamlit or static HTML)
- [ ] Data quality monitoring (row counts, null rates, staleness)
- [ ] Documentation (module-level docstrings, README usage guide)

### Phase 5 — Scale (if needed)
- [ ] Backfill historical data
- [ ] Add more sources
- [ ] Predictive models (delay prediction, rate forecasting)
- [ ] Cloud deployment (if data outgrows local)

---

## Immediate Next Steps

1. ✓ Data sources vetted — see [`staging/DATA_SOURCES.md`](DATA_SOURCES.md)
2. Set up Python project scaffold (`pyproject.toml`, source tree, test tree)
3. Build the storage layer (DuckDB + Parquet)
4. Implement first collector (Axiomancer Overwatch — no auth, instant AIS)
5. Build curation layer for dedup + validation

---

*This is a living document — update as sources, priorities, and architecture evolve.*
