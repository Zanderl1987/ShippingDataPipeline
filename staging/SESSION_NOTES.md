# Session Notes

## 2026-07-23 — Session 7

### Starting state
- All 90 tests pass (`pytest -v`)
- `ruff check .` clean
- `mypy src/` clean
- Phases 1-3 complete (storage, 9 collectors, curation, analytics, CLI)

### Session plan
- [x] Create integration tests for collector-to-storage flow
- [x] Update DATA_SOURCES.md with probe results
- [x] Commit and push changes

### Session results
- **101 tests total** (90 existing + 11 new), all passing
- `ruff check .` clean
- `mypy src/` clean
- New files:
  - `tests/test_integration.py` — 11 integration tests
- Modified files:
  - `src/storage/writer.py` — Added `_get_table_columns()` helper, `write_raw()` now filters DataFrame columns to match table schema
  - `src/collectors/global_fishing_watch.py` — Added `source` and `partition_date` columns to `_parse_vessel_search`
  - `src/collectors/shiplookup.py` — Added `source` and `partition_date` columns to `_parse_ship_search` and `_parse_ship_detail`
  - `src/collectors/un_comtrade.py` — Added `source` and `partition_date` columns to `_parse_trade_data`
  - `src/collectors/barentswatch.py` — Fixed `str.to_datetime` crash on null `eta` column
  - `staging/DATA_SOURCES.md` — Updated source details with collector implementations
  - `staging/PLAN.md` — Updated Phase 2 completion status
  - `staging/SESSION_NOTES.md` — Updated Session 6 notes

### Integration tests coverage
- Axiomancer → `ais_positions` (global snapshot + port positions)
- VesselAPI → `port_calls` (port events)
- GFW → `vessels` + events
- ShipLookup → `vessels` (search + detail)
- UN Comtrade → `trade_flow`
- BarentsWatch → `ais_positions`
- Source tracking (success + error recording)

### Bugs found and fixed during integration testing
1. **`write_raw()` schema mismatch** — Parsers produced extra columns (e.g., `vessel_type`) not in table schemas, causing `BinderException`
   - Fix: Added `_get_table_columns()` helper, `write_raw()` now only inserts columns that exist in the target table
2. **Missing `source`/`partition_date` columns** — GFW, ShipLookup, UN Comtrade parsers didn't add these required columns
   - Fix: Added `source` and `partition_date` to all parsers that write to tables
3. **Null `eta` column crash** — BarentsWatch `_parse_positions` called `str.to_datetime` on null column
   - Fix: Added `dtype == pl.String` check before datetime parsing

### Next steps
1. Set up API keys in `.env` for live testing
2. GitHub Actions workflow for scheduled collection
3. Data quality monitoring (row counts, null rates, staleness)
4. Documentation (module-level docstrings, README usage guide)

---

## 2026-07-22 — Session 6

### Starting state
- All 60 tests pass (`pytest -v`)
- `ruff check .` clean
- `mypy src/` clean
- Phases 1-3 complete (storage, 3 collectors, curation, analytics, CLI)

### Session plan
- [x] Add 6 new data source collectors (GFW, VesselAPI, UN Comtrade, ShipLookup, BarentsWatch, NOAA MarineCadastre)
- [x] Write tests for all new collectors
- [x] Update config with new API key settings

### Session results
- **90 tests total** (60 existing + 30 new), all passing
- `ruff check .` clean
- `mypy src/` clean
- New files:
  - `src/collectors/global_fishing_watch.py` — GFW vessel search, events, port visits
  - `src/collectors/vesselapi.py` — Port events, vessel lookup
  - `src/collectors/un_comtrade.py` — Trade flow data
  - `src/collectors/shiplookup.py` — Ship registry search/lookup
  - `src/collectors/barentswatch.py` — Norwegian waters AIS positions
  - `src/collectors/noaa_marinecadastre.py` — US waters bulk AIS download
  - `tests/collectors/test_global_fishing_watch.py` — 6 tests
  - `tests/collectors/test_vesselapi.py` — 5 tests
  - `tests/collectors/test_un_comtrade.py` — 3 tests
  - `tests/collectors/test_shiplookup.py` — 6 tests
  - `tests/collectors/test_barentswatch.py` — 6 tests
  - `tests/collectors/test_noaa_marinecadastre.py` — 4 tests
- Modified files:
  - `src/config.py` — Added `barentswatch_token` setting

### API details verified
- **Global Fishing Watch** (`gateway.api.globalfishingwatch.org/v3`)
  - Bearer token auth (free registration)
  - `/v3/vessels/search` — vessel identity search
  - `/v3/events` — fishing, port visits, encounters, loitering
  - Historical from 2012 to ~5 days ago
- **VesselAPI** (`api.vesselapi.com/v1`)
  - Bearer token auth (free tier, 150 calls/month)
  - `/portevents` — port arrivals/departures
  - `/vessel/{id}` — vessel details
- **UN Comtrade** (`comtradeapi.un.org`)
  - Subscription key (free tier, 500 calls/day)
  - `/data/v1/get` — global trade flows by HS code
- **ShipLookup** (`shiplookup.com/api`)
  - API key auth (free tier, 1000 credits/month)
  - `/ships/search` — free search (no credits)
  - `/ships/{imo}` — detailed lookup (1 credit)
- **BarentsWatch** (`live.ais.barentswatch.no/v1`)
  - Bearer token auth (free registration)
  - Norwegian economic zone only
  - No fishing <15m, no leisure <45m
  - Data older than 14 days unavailable
- **NOAA MarineCadastre** (`coast.noaa.gov/data/marinecadastre/ais`)
  - No auth required (public bulk download)
  - US waters AIS from 2009 onward
  - GeoParquet format for recent years

### Issues encountered
- Polars `str.to_datetime` requires explicit format when timestamps contain timezone "Z"
  - Fixed by adding `"%Y-%m-%dT%H:%M:%SZ"` format string to all collectors
- Polars fails on `str.to_datetime` when column is all nulls
  - Fixed by checking `dtype == pl.String` before parsing datetime columns
- Unused imports in new collector files — fixed with `ruff --fix`

### Next steps
1. ~~Update `staging/DATA_SOURCES.md` with probe results~~ ✓ (Session 7)
2. ~~Commit and push changes~~ ✓ (Session 6 + 7)
3. ~~Consider integration tests for collector-to-storage flow~~ ✓ (Session 7)
4. Set up API keys in `.env` for live testing

---

## 2026-07-21 — Session 5

### Starting state
- All 44 tests pass (`pytest -v`)
- `ruff check .` clean
- `mypy src/` clean
- Source tracking complete

### Session plan
- [x] Create curation module (dedup, validation, enrichment)
- [x] Write tests for curation functions (16 new tests)

### Session results
- **60 tests total** (44 existing + 16 new), all passing
- `ruff check .` clean
- `mypy src/` clean
- New files:
  - `src/curation/dedup.py` — Deduplication for ais_positions, vessels, ports
  - `src/curation/validation.py` — Not-null, range, positive checks + table validators
  - `src/curation/enrichment.py` — AIS + vessel + port joins, curated tables
  - `src/curation/pipeline.py` — `run_curation()` orchestrator
  - `tests/curation/test_dedup.py` — 4 tests
  - `tests/curation/test_validation.py` — 8 tests
  - `tests/curation/test_enrichment.py` — 4 tests

### Curation module features
- **Deduplication**: Remove duplicates based on key columns
  - `deduplicate_ais_positions()` — mmsi + timestamp + source
  - `deduplicate_vessels()` — imo (primary key)
  - `deduplicate_ports()` — unlocode (primary key)
  - `deduplicate_table()` — Generic function for any table
- **Validation**: Data quality checks
  - `validate_not_null()` — No NULL values
  - `validate_range()` — Values within min/max
  - `validate_positive()` — All values positive
  - `validate_ais_positions()` — All AIS checks
  - `validate_vessels()` — All vessel checks
  - `validate_ports()` — All port checks
- **Enrichment**: Create enriched/curated tables
  - `enrich_ais_with_vessel_info()` — AIS + vessel metadata
  - `enrich_ais_with_port_info()` — AIS + destination port
  - `create_curated_ais_positions()` — Full curated AIS table
  - `create_curated_vessels()` — Vessels with computed fields
- **Pipeline**: `run_curation()` orchestrates all steps

### Issues encountered
- Unused imports in curation modules — removed
- Import ordering violations — fixed with `ruff --fix`
- mypy errors with `fetchone()` returning `tuple | None` — added `_get_count()` helper
- Test failures for vessels/ports dedup — `INSERT OR REPLACE` already handles upserts

### Next steps
1. Update `staging/PLAN.md` to reflect curation completion
2. Commit and push changes
3. Create integration tests for collector-to-storage flow

---

## 2026-07-21 — Session 4

### Starting state
- All 35 tests pass (`pytest -v`)
- `ruff check .` clean
- `mypy src/` clean
- 3 collectors + storage layer + analytics complete

### Session plan
- [x] Add source tracking (checkpoints, timestamps)
- [x] Create SourceTracker class for recording collection events
- [x] Integrate tracker into all 3 collectors
- [x] Write tests for SourceTracker (9 new tests)

### Session results
- **44 tests total** (35 existing + 9 new), all passing
- `ruff check .` clean
- `mypy src/` clean
- New files:
  - `src/storage/tracker.py` — SourceTracker class + TimedCollector context manager
  - `tests/storage/test_tracker.py` — 9 tests
- Modified files:
  - `src/storage/schema.py` — Added `source_tracking` table
  - `src/collectors/axiomancer.py` — Added tracker parameter to collect functions
  - `src/collectors/seafarer_index.py` — Added tracker parameter to collect functions
  - `src/collectors/open_meteo.py` — Added tracker parameter to collect functions

### Source tracking features
- `source_tracking` table tracks: source, collection_ts, rows_fetched, rows_written, status, error_message, duration_ms
- `SourceTracker` class provides:
  - `record_collection()` — Record a collection event
  - `get_last_collection()` — Get most recent successful collection
  - `get_collection_history()` — Get recent collection history
  - `get_all_sources_status()` — Get status for all sources
  - `get_staleness_hours()` — Get hours since last successful collection
- `TimedCollector` context manager automatically times collections and records results
- All collectors accept optional `tracker` parameter for dependency injection

### Issues encountered
- Import ordering violations — fixed by sorting imports alphabetically
- Unused `datetime` import in test file — removed
- mypy error returning `Any` from `get_staleness_hours()` — fixed by adding explicit type annotation

### Next steps
1. Update `staging/PLAN.md` to reflect source tracking completion
2. Commit and push changes
3. Consider curation layer or integration tests

---

## 2026-07-21 — Session 3

### Starting state
- All 26 tests pass (`pytest -v`)
- `ruff check .` clean
- `mypy src/` clean
- 3 collectors + storage layer complete

### Session plan
- [x] Build analytics modules (routes, congestion, trade_flow)
- [x] Add CLI reporting entry point (`sdp` command)
- [x] Fix all lint/mypy errors across analytics modules
- [x] Write tests for analytics modules (9 new tests)

### Session results
- **35 tests total** (26 existing + 9 new), all passing
- `ruff check .` clean
- `mypy src/` clean
- New files:
  - `src/analytics/routes.py` — vessel tracks, route segments, active vessels
  - `src/analytics/congestion.py` — port activity, congestion estimation, dwell times
  - `src/analytics/trade_flow.py` — port pair analysis, destination summaries
  - `src/analytics/reports.py` — CLI entry point (`sdp` command)
  - `tests/analytics/test_routes.py` — 4 tests
  - `tests/analytics/test_congestion.py` — 3 tests
  - `tests/analytics/test_trade_flow.py` — 2 tests
- Updated `pyproject.toml` with `sdp` CLI entry point
- Fixed import issues: removed unused imports, added `MagicMock` type annotations

### Issues encountered
- `MagicMock` type annotations missing in test files — fixed by adding `from unittest.mock import MagicMock`
- Unused `datetime` import in `routes.py` — removed
- Unused `timedelta` import in `reports.py` — removed
- Line length violations (100 char limit) — reformatted long expressions
- mypy strict mode requiring `dict[str, Any]` instead of bare `dict` — added `Any` imports

### Next steps
1. Update `staging/PLAN.md` to reflect Phase 3 completion
2. Commit and push changes
3. Consider Phase 4 (curation layer, source tracking)

---

## 2026-07-21 — Session 2

### Starting state
- Cloned repo, `uv sync --extra dev` to install all deps
- All 8 tests pass (`pytest -v`)
- `ruff check .` clean
- `mypy src/` clean

### Completed (prior sessions)
- Project scaffold (folders, pyproject.toml, .gitignore, .env.example)
- `config.py` — env-based settings, `Settings.from_env()`, `ensure_dirs()`
- Storage layer — DuckDB schema (`schema.py`), Parquet writer (`writer.py`), reader (`reader.py`)
- Tests — `test_config.py` (3 tests), `test_storage.py` (5 tests)
- Data sources vetted — see `DATA_SOURCES.md`

### Session plan
- [x] Build Axiomancer Overwatch collector (free, no auth, AIS positions)
- [x] Build Seafarer Index collector (vessel + port registry)
- [x] Build Open-Meteo collector (marine + weather data)
- [x] Write tests for collectors (18 new tests)
- [x] Integration test: collector → storage flow

### Session results
- **26 tests total** (8 existing + 18 new), all passing
- `ruff check .` clean
- `mypy src/` clean
- New files:
  - `src/collectors/axiomancer.py` — global positions snapshot, port positions, vessel search
  - `src/collectors/seafarer_index.py` — ship registry, port registry
  - `src/collectors/open_meteo.py` — marine weather + weather forecasts
  - `src/storage/schema.py` — added `ports`, `marine_weather`, `weather` tables
  - `tests/collectors/test_axiomancer.py` — 6 tests
  - `tests/collectors/test_seafarer_index.py` — 6 tests
  - `tests/collectors/test_open_meteo.py` — 6 tests

### API details
- **Axiomancer Overwatch** (`axiomoverwatch.io/api/v1`)
  - `GET /positions/latest` — Global GeoJSON snapshot (~18K vessels), CDN-cached 5 min, no auth
  - `GET /positions?port={slug}` — Port-specific positions
  - `GET /vessels?port={slug}&type={type}` — Vessel search
  - Rate limit: Free tier 60 req/min
- **Seafarer Index** (`seafarerindex.com/api/data`)
  - `GET /ships` — Ship registry (~537KB JSON), CC BY 4.0
  - `GET /ports` — Port registry (~43.5MB JSON), CC BY 4.0
  - Static dumps, regenerated daily, no auth
- **Open-Meteo Marine** (`marine-api.open-meteo.com/v1/marine`)
  - `GET /v1/marine` — Wave height/direction/period, swell, ocean currents, SST
  - 7-day forecast, hourly resolution, no auth
  - Free for non-commercial use
- **Open-Meteo Weather** (`api.open-meteo.com/v1/forecast`)
  - `GET /v1/forecast` — Wind, pressure, temperature, precipitation
  - 16-day forecast, hourly resolution, no auth
  - Free for non-commercial use
- **OpenAIS** — Self-hosted only, no public API instance. Deferred.

---
