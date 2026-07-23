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
├── staging/                  # Planning & reference docs
│   ├── PLAN.md
│   ├── DATA_SOURCES.md
│   └── SESSION_NOTES.md
├── src/
│   ├── __init__.py
│   ├── config.py             # Central config (API keys, paths, schedules)
│   ├── collectors/           # One module per data source
│   │   ├── __init__.py
│   │   ├── axiomancer.py     # Axiomancer Overwatch (AIS positions)
│   │   ├── seafarer_index.py # Seafarer Index (ships, ports)
│   │   ├── open_meteo.py     # Open-Meteo (marine + weather)
│   │   ├── global_fishing_watch.py # Global Fishing Watch (vessels, events)
│   │   ├── vesselapi.py      # VesselAPI (port events, vessel lookup)
│   │   ├── un_comtrade.py    # UN Comtrade (trade flows)
│   │   ├── shiplookup.py     # ShipLookup (vessel registry)
│   │   ├── barentswatch.py   # BarentsWatch (Norwegian waters AIS)
│   │   └── noaa_marinecadastre.py # NOAA MarineCadastre (US waters AIS)
│   ├── storage/              # DuckDB schema, Parquet IO, partitioning
    │   │   ├── __init__.py
    │   │   ├── schema.py         # Table schemas (ais_positions, vessels, ports, marine_weather, weather, source_tracking)
    │   │   ├── reader.py         # Query + read dataset functions
    │   │   ├── writer.py         # Write raw + curated data
    │   │   └── tracker.py        # Source tracking (checkpoints, timestamps)
    │   ├── curation/             # Dedup, validation, enrichment (Phase 2)
    │   │   ├── __init__.py
    │   │   ├── dedup.py          # Deduplication functions
    │   │   ├── validation.py     # Data quality checks
    │   │   ├── enrichment.py     # Enrichment & curated tables
    │   │   └── pipeline.py       # Curation orchestrator
    │   └── analytics/            # Analysis & reporting modules (Phase 3)
    │       ├── __init__.py
    │       ├── routes.py         # Vessel tracks, route segments, active vessels
    │       ├── congestion.py     # Port activity, congestion estimation, dwell times
    │       ├── trade_flow.py     # Port pair analysis, destination summaries
    │       └── reports.py        # CLI entry point (`sdp` command)
├── tests/
│   ├── test_config.py
│   ├── test_storage.py
│   ├── collectors/
│   │   ├── test_axiomancer.py
│   │   ├── test_seafarer_index.py
│   │   ├── test_open_meteo.py
│   │   ├── test_global_fishing_watch.py
│   │   ├── test_vesselapi.py
│   │   ├── test_un_comtrade.py
│   │   ├── test_shiplookup.py
│   │   ├── test_barentswatch.py
│   │   └── test_noaa_marinecadastre.py
│   ├── storage/
│   │   └── test_tracker.py
│   ├── curation/
│   │   ├── test_dedup.py
│   │   ├── test_validation.py
│   │   └── test_enrichment.py
│   └── analytics/
│       ├── test_routes.py
│       ├── test_congestion.py
│       └── test_trade_flow.py
├── notebooks/                # Exploration & analysis notebooks
│   └── .gitkeep
├── data/                     # Local data (gitignored)
├── storage/                  # DuckDB database & partitioned Parquet
├── .env.example
├── .gitignore
├── pyproject.toml
└── uv.lock
```

---

## Phased Build Plan

### Phase 1 — Foundation ✓ COMPLETE
- [x] Set up project scaffold (folders, config, `.gitignore`, `pyproject.toml`)
- [x] Implement core storage layer (DuckDB schema, Parquet writer/reader)
- [x] Implement `config.py` with env-based settings
- [x] Write initial tests for storage layer
- [x] Set up `ruff`/`mypy`/`pytest` toolchain

### Phase 2 — Data Sources (in progress)
- [x] Vet & integrate first data source — **Axiomancer Overwatch** (free, no auth, AIS positions)
- [x] Vet & integrate **Seafarer Index** (vessel + port registry)
- [x] Vet & integrate **Open-Meteo** (marine + weather data)
- [ ] Vet & integrate **OpenAIS** (self-hosted only, deferred)
- [x] Add source tracking (checkpoints, timestamps)
- [x] Write curation layer (dedup + validation + enrichment)
- [x] Vet & integrate **Global Fishing Watch** (free token, 2012+ AIS + events)
- [x] Vet & integrate **VesselAPI** (free tier, port events + vessel lookup)
- [x] Vet & integrate **UN Comtrade** (free API key, global trade flows)
- [x] Vet & integrate **ShipLookup API** (free 1K credits/month, vessel registry)
- [x] Vet & integrate **BarentsWatch** (Norwegian waters AIS, open data)
- [x] Vet & integrate **NOAA MarineCadastre** (US waters historical AIS, bulk download)
- [ ] Integration tests for collector-to-storage flow

### Phase 3 — Analytics ✓ COMPLETE
- [x] Build route mapping from AIS position sequences
- [x] Build port congestion metrics (wait times, throughput)
- [x] Build trade flow summaries (port-to-port volumes)
- [x] Add CLI reporting (`sdp` command)
- [x] Unit tests for each analytic

### Phase 4 — Automation & Polish (in progress)
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
2. ✓ Set up Python project scaffold (`pyproject.toml`, source tree, test tree)
3. ✓ Build the storage layer (DuckDB + Parquet)
4. ✓ Implement first collector (Axiomancer Overwatch — no auth, instant AIS)
5. ✓ Implement second collector (Seafarer Index — vessel + port registry)
6. ✓ Implement Open-Meteo collector (marine + weather data)
7. ✓ Build analytics modules (routes, congestion, trade_flow)
8. ✓ Add CLI reporting (`sdp` command)
9. ✓ Build curation layer (dedup + validation + enrichment)
10. ✓ Add source tracking (checkpoints, timestamps)
11. Create integration tests for collector-to-storage flow
12. Research and add new data sources

---

*This is a living document — update as sources, priorities, and architecture evolve.*
