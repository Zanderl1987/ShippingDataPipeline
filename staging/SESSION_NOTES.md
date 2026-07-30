# Session Notes

## 2026-07-30 — Session 13

### Starting state
- Repo code-complete (16 collectors, schema, storage, CLI, tests) but **had collected zero data**
- Every table in `storage/pipeline.db` empty, 0 parquet files, `source_tracking` empty
- Only 11 of the 18 tables in `schema.py` existed in the DB
- Goal: land real data to feed the combined data-lake at `C:\Users\zande\data-lake`
- Priority tables: `chokepoint_transits`, `chokepoint_status`, `freight_rates`

### Session plan
- [x] Investigate why the pipeline persisted nothing
- [x] Fix the systemic blockers (PR #1)
- [x] Repair the individual broken no-auth collectors (PR #2)
- [x] Record NO-GO verdicts for sources that cannot work
- [x] Update session notes and task list

### Root causes — why zero rows
Five bugs, three of them silent:

1. **`init_db()` was never called** anywhere in the collection flow. Only 11 of 18 tables existed; the priority tables and all `oil_*` tables were missing entirely.
2. **`write_raw()` silently returned 0** when the target table did not exist — the insert-column list came back empty and it returned early. Runs were then recorded as `status='success', rows_written=0`. This is what hid the failure: the first diagnostic run logged `imf_portwatch … fetched=26261, written=0, success`.
3. **IMF PortWatch date parsing** — the ArcGIS `date` field is an ISO string (`"2024-06-01"`), but the parser assumed epoch milliseconds, so every `transit_date` became `""` and the DATE cast failed.
4. **Eagle Intelligence wrote to the wrong table** — `write_raw()` called without `table_name`, defaulting to `ais_positions`, where all chokepoint columns were dropped.
5. **Phase 7 collectors silently unregistered** — `collect_all.py` imported names that do not exist (`collect_freight_rates`, `collect_dma_vessels`, …); the real entry point is `collect_data`. The `ImportError` was swallowed at debug level.

A sixth surfaced while repairing collectors:

6. **Schema drift** — `ais_positions` lacked `vessel_type`. `CREATE TABLE IF NOT EXISTS` never alters an existing table, so `write_raw` dropped the column on every insert. Migration `20260730001` added. Audited all 18 tables; this was the only drift.

### Collector repairs
| Source | Was | Now |
|---|---|---|
| imf_portwatch | 0 rows (missing table + date bug) | 77,389 |
| eagle_intelligence | wrote to `ais_positions` | 6 |
| axiomancer | HTTP 400 — API needs viewport bounds | 59,107 |
| tankermap | `'list' object has no attribute 'get'` — returns a bare array | 5,000 |
| jodi_oil | HTTP 404 — now a zipped CSV at a new path, all columns renamed | 1,648,728 |

### Data-quality bugs found in JODI
- Its `-` (not available), `x` (confidential) and `N/A` placeholders were coerced to `0.0` by a bare `except`, **turning non-reports into real-looking zeroes**. Now dropped, preserving the 274,536 genuine reported zeroes as distinct.
- JODI publishes five units but `oil_trade` only has mass and volume columns; `KBD` (a rate) and `KL` (kilolitres) map to neither, so those rows persisted with both quantity columns null. Now skipped.
- Combined: 6.89M → 1.65M rows, none empty.

### Table state at session end
| Table | Rows |
|---|---|
| `oil_trade` | 1,648,728 |
| `chokepoint_transits` | 77,389 (2019-01-01 → 2026-07-26) |
| `ais_positions` | 64,107 (axiomancer 59,107 + tankermap 5,000) |
| `marine_weather` | 240 |
| `chokepoint_status` | 6 |

### Sources confirmed dead (NO-GO)
`dma`, `barcelona_port`, `singapore_oceanx`, `fbx` all target endpoints that **return 404**; `equasis` needs a login and an IMO list. All five were listed as "✅ Collector built" despite never having been called successfully. Recurring tell: a `wp-json/<invented-namespace>/v1/<resource>` route on a WordPress marketing site (Barcelona and FBX both used it; neither exists).

`freight_rates` therefore has **no working free source** — Drewry WCI 429s with HTML, the FBX `wp-json` route returns the WordPress page. It would need a registered/paid API.

### Commits / PRs
- `cbab028` — systemic collection fixes → [PR #1](https://github.com/Zanderl1987/ShippingDataPipeline/pull/1)
- `62c9b1d` — collector repairs + `vessel_type` migration → [PR #2](https://github.com/Zanderl1987/ShippingDataPipeline/pull/2), stacked on #1
- Merged `origin/main` into the branch mid-session; it was behind by the Session 12 doc commit, which touched `REMAINING_WORK.md`.

### Issues encountered
- `write_raw`'s silent `return 0` made a total failure look like success — changed to raise on a genuine column mismatch. This was the single most expensive bug of the session.
- `apply_pending_migrations` swallows per-statement failures at `logger.debug` **and still records the migration as applied**, so a failed `ALTER` would look successful. Verified the new column empirically rather than trusting the log. Not fixed.
- Partitioned inserts have **no dedup**; repeated test runs duplicated rows. Deduped `chokepoint_transits`/`chokepoint_status` by natural key and reloaded `oil_trade`.
- `dashboard.py` `_build_html` returns a tuple → 4 pre-existing test failures. Unrelated to collection; spun out to its own task.

### Test status
**210 passed** (up from 205 — 5 new JODI cases for renamed columns, placeholder handling and unit routing). 4 failures remain, all the pre-existing `dashboard.py` tuple bug. `ruff` clean.

### Next steps
1. **Add dedup to partitioned inserts before enabling the daily schedule** — a weekly JODI run currently appends another 1.65M duplicate rows.
2. Merge PR #1, then PR #2.
3. Fix `apply_pending_migrations` swallowing failed statements while marking migrations applied.
4. Research real endpoints for DMA (`web.ais.dk/aisdata/`) and Singapore MPA, or drop those collectors.
5. Register remaining free API keys (GFW, VesselAPI, ShipLookup, UN Comtrade, BarentsWatch) to activate the key-gated collectors.

---

## 2026-07-30 — Session 12

### Starting state
- 208 tests pass, `ruff check .` clean, `mypy src/` clean
- Phase 4 complete (automation, monitoring, notifications)
- EIA + AISStream API keys obtained, `.env` configured, GitHub secrets set
- Workflow triggered once — failed on ruff lint errors

### Session plan
- [x] Fix remaining ruff violations that `--fix` couldn't resolve
- [x] Commit fixes and re-trigger workflow
- [x] Update session notes, REMAINING_WORK.md, PLAN.md

### Session results
- `ruff check .` clean (all 4 remaining E501 + 1 F821 + 1 F841 manually fixed)
- Commits:
  - `e911642` — Add EIA + AISStream API keys to .env and GitHub secrets, mark Hormuz Monitor as NO-GO
  - `ba24901` — Fix all ruff lint violations (E501, F821, F401, F541, I001, UP037)
- Workflow re-triggered at `https://github.com/Zanderl1987/ShippingDataPipeline/actions/runs/30488432647`

### Lint fixes applied
1. **E501 (long lines)** — equasis_collector, noaa_marinecadastre, backfill, collect_all (2x), dashboard (2x), migrations
2. **F821 (undefined name)** — `SourceTracker` in pipeline.py: moved import to module level, removed lazy re-import
3. **F841 (unused variable)** — `result = conn.execute(...)` in lineage.py: removed assignment

### Key updates
- **Hormuz Monitor** confirmed NO-GO (webpage claims paid plans only, no free tier) — documented in DATA_SOURCES.md and removed from workflow
- `.env` secrets now set for EIA_API_KEY and AISSTREAM_API_KEY
- GitHub secrets set for both keys
- Remaining keys still needed: GFW_API_TOKEN, VESSELAPI_API_KEY, SHIPLOOKUP_API_KEY, UN_COMTRADE_API_KEY, BARENTSWATCH_TOKEN

### Next steps
1. Monitor workflow run for passing status
2. Register remaining free API keys (GFW, VesselAPI, ShipLookup, UN Comtrade, BarentsWatch)
3. Update DATA_SOURCES.md with new oil sources
4. Document ADVERSARIAL_REVIEW.md findings in PLAN.md

---

## 2026-07-27 — Session 11

### Starting state
- 204 tests pass, `ruff check .` clean, `mypy src/` clean
- Phase 4 complete (automation, monitoring, notifications)
- All 16 collectors registered and importable

### Session plan
- [x] Code review: fix P0 collector crashes (open_meteo, global_fishing_watch, un_comtrade)
- [x] Code review: fix row count bug in run_collector
- [x] Code review: fix EIA column mismatch for supply/imports collectors
- [x] Code review: fix VesselAPI missing source column
- [x] Code review: fix UN_COMITRADE_API_KEY typo (5 files)
- [x] Adversarial review of all fixes
- [x] Fix adversarial findings (README/.env typo remnants, row count regression, vesselapi removal)
- [x] Fix SQL injection in reader.py (table name allowlist)
- [x] Add module-level docstrings to all 37 source files
- [x] Build static HTML dashboard generator (`sdp dashboard` command)
- [x] Write dashboard tests

### Session results
- **208 tests total** (204 existing + 4 new), all passing
- `ruff check .` clean
- `mypy src/` clean (0 issues in 38 source files)
- Commits:
  - `948a61b` — Fix P0 collector crashes, row count bug, EIA schema mismatch, and UN_COMTRADE typo
  - `44bc7a1` — Add SQL injection fix, module docstrings, and HTML dashboard

### Bugs fixed
1. **Collector crashes (P0)** — open_meteo, global_fishing_watch, un_comtrade collectors crashed on missing required args in `get_collectors()`. Fixed by wrapping in lambdas with sensible defaults.
2. **Row count bug (P0)** — `run_collector()` read from tracker before collector wrote to it. Restored read-after-collect pattern (works because collector's TimedCollector writes to same DuckDB file).
3. **EIA column mismatch (P1)** — `collect_weekly_supply()` and `collect_monthly_imports()` produced columns that didn't match `oil_inventories` schema. Added `_parse_eia_supply_response()` parser.
4. **VesselAPI missing source (P1)** — `_parse_vessel()` didn't add `source` or `partition_date` columns. Added them.
5. **UN_COMTRADE_API_KEY typo (P1)** — `UN_COMITRADE_API_KEY` misspelled in 5 files (config, collector, workflow, README, .env.example).
6. **SQL injection (MEDIUM)** — `read_dataset()` and `get_latest_timestamp()` used f-string interpolation for table names. Added allowlist validation from `ALL_TABLES`.

### New files
- `src/monitoring/dashboard.py` — Static HTML dashboard generator
- `tests/monitoring/test_dashboard.py` — 4 dashboard tests

### Modified files
- `src/storage/reader.py` — Added `VALID_TABLE_NAMES` allowlist and `_validate_table_name()` helper
- `src/monitoring/collect_all.py` — Fixed collector registration, row count tracking, vesselapi re-registration
- `src/collectors/eia_petroleum.py` — Added `_parse_eia_supply_response()`, updated supply/imports collectors
- `src/collectors/vesselapi.py` — Added `source`/`partition_date` to `_parse_vessel()`
- `src/config.py` — Fixed `UN_COMTRADE_API_KEY` typo
- `src/collectors/un_comtrade.py` — Fixed error message typo
- `.github/workflows/collect.yml` — Fixed secret name
- `README.md` — Fixed env var name
- `.env.example` — Fixed env var name
- `src/analytics/reports.py` — Added `sdp dashboard` command
- All 37 source files — Added module-level docstrings

### CLI commands
| Command | Description |
|---------|-------------|
| `sdp overview` | Show pipeline overview |
| `sdp vessels` | Show active vessels |
| `sdp ports` | Show port activity |
| `sdp congestion` | Show congestion estimates |
| `sdp destinations` | Show vessel destinations |
| `sdp routes` | Show port-to-port routes |
| `sdp weather` | Show recent weather data |
| `sdp status` | Show pipeline health status |
| `sdp collect` | Run data collection |
| `sdp quality` | Show detailed quality metrics |
| `sdp dashboard` | Generate HTML dashboard |

### Next steps
1. Register for API keys (EIA, Hormuz, AISStream)
2. Set up GitHub repository secrets for API keys
3. Test workflow on GitHub Actions (first run)

---

## 2026-07-23 — Session 10

### Starting state
- 176 tests pass, `ruff check .` clean, `mypy src/` clean
- Phase 2 complete (16 collectors + integration tests)
- Oil sources: EIA, JODI-Oil, IMF PortWatch, TankerMap, Hormuz Monitor

### Session plan
- [x] Create GitHub Actions workflow for scheduled collection
- [x] Build notification system (Slack/Discord webhook, email, log file)
- [x] Build data quality monitoring (row counts, null rates, staleness)
- [x] Enhance CLI with `sdp status`, `sdp collect`, `sdp quality` commands
- [x] Add notification settings to config
- [x] Write tests for all new monitoring modules

### Session results
- **204 tests total** (176 existing + 28 new), all passing
- `ruff check .` clean
- `mypy src/` clean
- New files:
  - `.github/workflows/collect.yml` — GitHub Actions workflow (daily collection, quality gate)
  - `src/monitoring/__init__.py` — Monitoring module
  - `src/monitoring/quality.py` — Data quality checks (row counts, null rates, staleness)
  - `src/monitoring/notify.py` — Notification system (webhook, email, log file)
  - `src/monitoring/collect_all.py` — Collection orchestrator
  - `tests/monitoring/test_quality.py` — 9 tests
  - `tests/monitoring/test_notify.py` — 9 tests
  - `tests/monitoring/test_collect_all.py` — 10 tests
- Modified files:
  - `src/config.py` — Added notification settings (Slack, Discord, email, SMTP)
  - `src/analytics/reports.py` — Added `sdp status`, `sdp collect`, `sdp quality` commands
  - `.env.example` — Added notification settings

### GitHub Actions workflow
- Runs daily at 06:00 UTC
- Manual trigger with source selection and force options
- Steps: lint → typecheck → test → collect → quality check → artifact upload
- Quality gate job runs after collection
- API keys stored as GitHub secrets

### CLI commands
| Command | Description |
|---------|-------------|
| `sdp overview` | Show pipeline overview |
| `sdp vessels` | Show active vessels |
| `sdp ports` | Show port activity |
| `sdp congestion` | Show congestion estimates |
| `sdp destinations` | Show vessel destinations |
| `sdp routes` | Show port-to-port routes |
| `sdp weather` | Show recent weather data |
| `sdp status` | Show pipeline health status |
| `sdp collect` | Run data collection |
| `sdp quality` | Show detailed quality metrics |

### Next steps
1. Set up GitHub repository secrets for API keys
2. Test workflow on GitHub Actions (first run)
3. Optional: lightweight dashboard (Streamlit or static HTML)
4. Documentation (README usage guide)

### Git
- Commit: `a3c11d9` — 27 files, +3,843 lines
- Message: "Session 9-10: Add oil collectors + Phase 4 automation (204 tests)"
- Pushed to `origin/main`

---

## 2026-07-23 — Session 9

### Starting state
- 122 tests pass, `ruff check .` clean, `mypy src/` clean
- Phase 2 complete (11 collectors + integration tests)
- Data sources vetted: Axiomancer, SeafarerIndex, Open-Meteo, GFW, VesselAPI, UN Comtrade, ShipLookup, BarentsWatch, NOAA MarineCadastre, Eagle Intelligence, AISStream

### Session plan
- [x] Research free oil shipping data sources
- [x] Build collectors for all viable oil sources
- [x] Add new table schemas for oil-specific data
- [x] Write tests for all new collectors
- [x] Update DATA_SOURCES.md, PLAN.md, SESSION_NOTES.md

### Session results
- **176 tests total** (122 existing + 54 new), all passing
- `ruff check .` clean
- `mypy src/` clean
- New files:
  - `src/collectors/eia_petroleum.py` — US petroleum stocks, refinery, imports (free API key)
  - `src/collectors/jodi_oil.py` — Global oil production, consumption, trade (free CSV)
  - `src/collectors/imf_portwatch.py` — Chokepoint transit counts + capacity (free ArcGIS API)
  - `src/collectors/tankermap.py` — Live tanker positions, port calls (free, no auth)
  - `src/collectors/hormuz_monitor.py` — Risk score, oil prices, VLCC rates (free tier)
  - `tests/collectors/test_eia_petroleum.py` — 11 tests
  - `tests/collectors/test_jodi_oil.py` — 10 tests
  - `tests/collectors/test_imf_portwatch.py` — 8 tests
  - `tests/collectors/test_tankermap.py` — 13 tests
  - `tests/collectors/test_hormuz_monitor.py` — 12 tests
- Modified files:
  - `src/storage/schema.py` — Added `oil_inventories`, `chokepoint_transits`, `oil_prices`, `oil_trade` tables
  - `src/config.py` — Added `eia_api_key`, `hormuz_api_key` settings
  - `src/collectors/axiomancer.py` — Added `collect_tankers()` convenience function

### New table schemas
| Table | Purpose | Partitioned By |
|-------|---------|----------------|
| `oil_inventories` | US crude stocks, refinery data (EIA) | report_date, source |
| `chokepoint_transits` | Daily vessel transits + capacity (PortWatch, Hormuz) | transit_date, source |
| `oil_prices` | Brent/WTI/Dubai prices, VLCC rates (Hormuz Monitor) | price_date, source |
| `oil_trade` | Global oil production/trade (JODI-Oil) | period, source |

### Next steps
1. Register for EIA API key (free, eia.gov)
2. Register for Hormuz Monitor API key (free, hormuzmonitor.com)
3. Set up API keys in `.env` for live testing
4. Integration tests for new oil collectors
5. GitHub Actions workflow for scheduled collection

---

## 2026-07-23 — Session 8

### Starting state
- 101 tests pass, `ruff check .` clean, `mypy src/` clean
- Phase 2 complete (9 collectors + integration tests)
- Data sources vetted: Axiomancer, SeafarerIndex, Open-Meteo, GFW, VesselAPI, UN Comtrade, ShipLookup, BarentsWatch, NOAA MarineCadastre

### Session plan
- [x] Vet new free shipping data sources
- [x] Build collectors for viable sources
- [x] Update DATA_SOURCES.md, PLAN.md, SESSION_NOTES.md

### Session results
- **122 tests total** (101 existing + 21 new), all passing
- `ruff check .` clean
- `mypy src/` clean
- New files:
  - `src/collectors/eagle_intelligence.py` — Eagle Intelligence chokepoint risk collector
  - `src/collectors/aisstream.py` — AISStream real-time WebSocket AIS collector
  - `tests/collectors/test_eagle_intelligence.py` — 13 tests
  - `tests/collectors/test_aisstream.py` — 8 tests
- Modified files:
  - `src/storage/schema.py` — Added `chokepoint_status` table schema
  - `staging/DATA_SOURCES.md` — Added Eagle Intelligence entry, NO-GO list
  - `staging/PLAN.md` — Marked Phase 2 complete with new collectors
- Commits:
  - `8b2084b` — Add Eagle Intelligence & AISStream collectors, chokepoint risk table, 21 new tests

### Source vetting results
| Source | Verdict | Reason |
|--------|---------|--------|
| **Eagle Intelligence** | **GO** | Free, no auth, JSON API, 6 chokepoints, CC BY 4.0 |
| **AISStream.io** | **GO** | Free with registration, WebSocket AIS stream |
| HormuzMonitor.com | NO-GO | 401 without valid key, unclear free tier |
| emissions.dev | NO-GO | 401 without valid key, needs registration |
| Sinay.ai | NO-GO | 401 without key |
| FreightPulse | NO-GO | Returns HTML landing page, API may not be live |

### Eagle Intelligence details
- **API**: `https://eagleintelmari.com/api/chokepoint-status` (all 6), `/api/hormuz-status` (Hormuz)
- **Auth**: None required (attribution: CC BY 4.0)
- **Rate limit**: 1 req/min fair use
- **Data**: Status tier (SEVERE/ELEVATED/MONITORING), signal counts (24h/7d), HIGH-severity headlines, crisis-day counters
- **Chokepoints**: Hormuz, Suez, Bab el-Mandeb, Panama, Malacca, Bosphorus
- **Storage**: `chokepoint_status` table (partitioned by date + source)

### AISStream details
- **API**: WebSocket at `wss://stream.aisstream.io/v0/stream`
- **Auth**: API key (free registration via GitHub)
- **Data**: Real-time AIS position reports, vessel static data (name, IMO, callsign, dimensions)
- **Coverage**: Global terrestrial AIS (coastal + port areas)
- **Collection**: Bounding box subscription, optional MMSI filter, configurable duration
- **Storage**: Positions → `ais_positions` table, vessels → `vessels` table

### Next steps
1. Register for AISStream API key (free, via GitHub)
2. Register for remaining APIs (emissions.dev, Sinay) if needed
3. Set up API keys in `.env` for live testing
4. GitHub Actions workflow for scheduled collection

---

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
1. ~~Set up API keys in `.env` for live testing~~ — Partially done (Eagle Intelligence needs no key)
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
