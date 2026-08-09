# Remaining Work

## Data Collection — pipeline now lands data (Session 13)

The pipeline had collected **zero rows** until Session 13. Six bugs fixed across
[PR #1](https://github.com/Zanderl1987/ShippingDataPipeline/pull/1) and
[PR #2](https://github.com/Zanderl1987/ShippingDataPipeline/pull/2).

| Task | Details | Status |
|------|---------|--------|
| Call `init_db()` before collecting | Only 11 of 18 tables existed; the rest were never created | ✅ Done |
| Stop `write_raw()` returning 0 silently | Missing table made total failure look like `success` | ✅ Done |
| Fix IMF PortWatch date parsing | ArcGIS returns an ISO string, parser assumed epoch ms | ✅ Done — 77,389 rows |
| Route Eagle Intelligence writes | Defaulted to `ais_positions`, dropping all chokepoint columns | ✅ Done — 6 rows |
| Register Phase 7 collectors | Imported names that don't exist; entry point is `collect_data` | ✅ Done |
| Fix axiomancer | API 400s without viewport bounds | ✅ Done — 59,107 rows |
| Fix tankermap | Endpoint returns a bare JSON array, not a dict | ✅ Done — 5,000 rows |
| Fix jodi_oil | Moved to zipped CSV, every column renamed | ✅ Done — 1,648,728 rows |
| Add `ais_positions.vessel_type` | Schema drift: `CREATE TABLE IF NOT EXISTS` never alters | ✅ Done — migration `20260730001` |
| Dedup on partitioned inserts | `TableSchema.dedup_keys` + delete-then-insert upsert; parquet partitions re-exported from DuckDB so the two stores can't drift | ✅ Done — collectors verified idempotent |
| Fix migration runner swallowing failures | A failed statement now aborts the migration, is logged at `error`, and is left unrecorded so it retries | ✅ Done |
| Fix `dashboard.py` `_build_html` | Stray trailing comma made the return a tuple → `write_text` TypeError | ✅ Done |

Re-running a collector is now a no-op. Verified against live endpoints:
`chokepoint_transits` 77,389 → 77,389, `chokepoint_status` 6 → 6,
`ais_positions` 59,071 → 59,071, with parquet matching DuckDB exactly.

### Priority tables for the data-lake join

| Table | Rows | Source |
|-------|------|--------|
| `chokepoint_transits` | 77,389 (2019-01-01 → 2026-07-26) | IMF PortWatch |
| `chokepoint_status` | 6 | Eagle Intelligence |
| `freight_rates` | 0 | OilPriceAPI indices (BDI/BCI/SCFI/WCI) wired weekly, but plan-gated — expect 0 until a paid key. Container per-route rates still need a source. |

## Zero-row tables now landing data (Session 16, 2026-08-09)

| Table | Source | Verdict |
|-------|--------|---------|
| `vessels` | Digitraffic (`digitraffic_vessels`, weekly) | ✅ ~890 rows; fixed stale `imo PRIMARY KEY` migration `202608090001` that had blocked every vessel-writing collector |
| `port_calls` | Digitraffic (`digitraffic_port_calls`, weekly) | ✅ ~200 rows (Finnish ports) |
| `ais_positions` | Digitraffic (`digitraffic`, daily) | ✅ ~1,020 live Baltic positions/day |
| `oil_prices` | OilPriceAPI (`oilpriceapi_oil`, daily) | ✅ BRENT/WTI/DUBAI — needs `OILPRICEAPI_API_KEY` |
| `freight_rates` | OilPriceAPI (`oilpriceapi_freight`, weekly) | ⚠️ wired but plan-gated; tolerated |
| `ports` | Digitraffic (`digitraffic_ports`, weekly) | ✅ **12,256 rows** — `ssnLocations` global port/location reference (18,660 locations, 12,256 with coordinates kept). Fills the `ports` PK table via `INSERT OR REPLACE`. |
| `weather` | Open-Meteo (`open_meteo_weather`, daily) | ✅ **72 rows** — root cause: `collect_weather` existed but was never registered (same orphaned-collector pattern as aisstream). Now wired at Singapore. |

Still 0 rows — all key-gated (need a key registered in `.env`), not bugs: `oil_prices`/`freight_rates` (OilPriceAPI), `oil_inventories` (EIA `collect_weekly_stocks` wired, `EIA_API_KEY` not in `.env`), `trade_flow` (UN Comtrade `un_comtrade_api_key`), `marine_weather` (open_meteo, no key needed but last landed 2026-08-03 — see below). NO-GO sources target `vessel_registry` (DMA, dead) and `vessel_safety` (Equasis, needs auth).

> `marine_weather` note: the open_meteo marine collector is wired and key-free but last landed 2026-08-03 (120 rows). The daily run at 06:00 UTC overwrites the same (timestamp, lat, lon, source) key with the Singapore point each day; a re-run after 2026-08-03 would refresh it. Same fix as `weather` — no code change needed, just the schedule hitting it.

## API Key Setup

| Task | Details | Status |
|------|---------|--------|
| Register for EIA API key | Free at eia.gov/open, needed for `eia_petroleum.py` | ✅ Done |
| Register for AISStream API key | Key registered — **but `aisstream` is not in `get_collectors()`, so it has never run**. See orphaned-collectors task. | ⚠️ Key done, collector unwired |
| Hormuz Monitor | Listed as having free tier but registration yields only paid plans. NO-GO. | ❌ No free tier |
| Set up `.env` with API keys | Added `EIA_API_KEY`, `AISSTREAM_API_KEY` | ✅ Done |
| Set up GitHub secrets | `EIA_API_KEY`, `AISSTREAM_API_KEY` set as repo secrets | ✅ Done |
| Test GitHub Actions workflow | **First green run 2026-07-31** — 12 succeeded / 0 failed, 1,790,531 rows. Previously 9 runs, 9 failures, never reached collection | ✅ Done |

## Phase 4 — Automation & Polish ✓ COMPLETE

| Task | Details | Status |
|------|---------|--------|
| GitHub Actions workflow | `.github/workflows/collect.yml` — daily at 06:00 UTC | ✅ Done |
| Notification on failures | Slack/Discord webhook, email, log file via `src/monitoring/notify.py` | ✅ Done |
| Data quality monitoring | `src/monitoring/quality.py` — row counts, null rates, staleness | ✅ Done |
| CLI reporting (`sdp` command) | Added `status`, `collect`, `quality`, `dashboard` commands | ✅ Done |
| Collection orchestrator | `src/monitoring/collect_all.py` — runs all collectors with staleness checks | ✅ Done |
| Static HTML dashboard | `src/monitoring/dashboard.py` — `sdp dashboard` generates HTML report | ✅ Done |
| Module docstrings | All 37 source files have module-level docstrings | ✅ Done |
| SQL injection fix | `reader.py` — table name allowlist validation | ✅ Done |

## Documentation

| Task | Details | Status |
|------|---------|--------|
| README usage guide | Install, configure, run collectors, query data | ✅ Done |
| Module-level docstrings | All 37 source files | ✅ Done |
| DATA_SOURCES.md update | Add new oil sources (EIA, JODI, PortWatch, TankerMap, Hormuz) | ✅ Done |
| DATA_SOURCES.md update | Add Barcelona, DMA, Equasis, FBX, Singapore MPA **and TradingEconomics** as **NO-GO**, not as working sources | Not started |

## Deferred / Low Priority

| Task | Details | Status |
|------|---------|--------|
| OpenAIS integration | Self-hosted only, deferred | Blocked |
| Baltic Exchange trial | 1-week free trial for freight rate data | Not started |
| Danish Maritime Authority (DMA) | `www.dma.dk/api/vessels/search` → 404, endpoint does not exist. Real AIS is at `web.ais.dk/aisdata/` (daily zipped CSVs) — needs a rewrite, not a URL swap. | ⚠️ Built, source dead |
| Singapore OCEANS-X | `www.mpa.gov.sg/api/vessel-traffic` → 404, endpoint does not exist. | ⚠️ Built, source dead |
| Barcelona Port Authority | `www.portdebarcelona.cat/wp-json/openinfo/v1/*` → 404 (bare domain also has no DNS). | ⚠️ Built, source dead |
| Equasis | Requires login plus an IMO list; logs "No IMO numbers configured". | ⚠️ Built, needs auth |
| FBX (Freightos Baltic Index) | Drewry WCI → 429 + HTML; FBX `wp-json` route returns the WordPress page. No free source for `freight_rates`. | ⚠️ Built, source dead |
| TradingEconomics (from `origin/master`) | Pages load fine (200, no WAF, no auth) but serve a **current value only** — history is behind the paid plan, guest API returns **410 discontinued**, and BDI/BCI/BPI/BSI are routeless dry-bulk *indices* that don't fit the `freight_rates` schema. License is personal-use. Returns HTTP 200 for missing pages. | ❌ NO-GO, spiked 2026-07-30 |
| Danish Maritime Authority AIS (from Session 16 probe) | `web.ais.dk/aisdata/` — SSL cert hostname mismatch; `verify=False` gives `RemoteDisconnected`. | ❌ NO-GO, probed 2026-08-09 |
| OECD maritime CO₂ emissions | Landing page 403; SDMX `GetData/MARITIME_CO2` endpoints 404. | ❌ NO-GO, probed 2026-08-09 |
| ICC IMB Piracy Reporting Centre | Piracy reports gated behind a map, no public API. | ❌ NO-GO, probed 2026-08-09 |
| NGI LNG Daily | Paid subscription, no free tier. | ❌ NO-GO, probed 2026-08-09 |
| Signal Ocean | `api.signal-ocean.com` does not resolve (no DNS). | ❌ NO-GO, probed 2026-08-09 |
| UP Indices (UPI) | Freemium model — API requires a paid plan. | ❌ NO-GO, probed 2026-08-09 |
| OEC API | `api.oec.world` 403 on all API versions (v2/v3/tesseract), any UA. | ❌ NO-GO, probed 2026-08-09 |
| SeaRates Freight Index | `freight-index.searates.com` no DNS; commercial API. | ❌ NO-GO, probed 2026-08-09 |
| Straits.live Hormuz API | `straits.live/api` 403 (WAF). | ❌ NO-GO, probed 2026-08-09 |
| THETIS-MRV community API | Data endpoints 500 (broken Supabase backend). | ❌ NO-GO, probed 2026-08-09 |
| `mou.mrl.dev` PSC API | 401 without HTTP Basic creds; one-off community project. | ❌ NO-GO, probed 2026-08-09 |
| Paris MoU inspection search | JS SPA, no public JSON API; bulk needs manual account request. | ⚠️ PROBE (account form) |
| Tokyo MoU PSC | Web only; guessed URLs 404. | ⚠️ PROBE (find real UI path) |
| EU Fleet Register | Fishing vessels only; captcha-gated results. | ❌ NO-GO for merchant `vessel_registry` |
| FRED for `freight_rates` | Free key, but no ocean container/Baltic rates — only US domestic indices. | ⚠️ GO for `oil_prices` backup only |

## New working sources (Session 16, 2026-08-09)

| Source | Endpoint | Auth | Status |
|--------|----------|------|--------|
| Digitraffic | `meri.digitraffic.fi` `/api/ais/v1/locations`, `/api/ais/v1/vessels`, `/api/port-call/v1/port-calls`, `/api/port-call/v1/ports` | None | ✅ GO — Baltic Sea AIS + global port reference, no key, open swagger |
| OilPriceAPI | `api.oilpriceapi.com` `/v1/prices/latest`, `/v1/prices/marine-fuels` | `Authorization: Token` (env `OILPRICEAPI_KEY`) | ✅ GO for oil benchmarks; freight indices plan-gated |
| UN/LOCODE official | `opensource.unicc.org/.../vocab-locode/-/jobs/artifacts/2025-1/download?job=package-release` (zip with 3 CSVs) | None | ✅ GO — verified live 2026-08-09, 116,533 rows. Canonical `ports` reference; replace Digitraffic-derived 12,256-row `ports` table. |

## New working sources (Session 16b sweep, 2026-08-09)

| Source | What | Auth | Status |
|--------|------|------|--------|
| UN/LOCODE official (UNECE) | Canonical global port/location reference, 116K rows | None | ✅ GO — verified live. Replaces Digitraffic-derived `ports`. |
| NOAA ERDDAP | Oceanographic gridded/tabular data (SST, wave height, currents, WW3) | None | ✅ GO — verified live. Complements Open-Meteo for `marine_weather`. |
| FRED | Oil benchmarks (WTI/Brent) — backup for `oil_prices` | Free API key | ✅ GO (needs signup) |
| FreightPulse | Per-route container rates (Shanghai→LA 40ft) + port congestion | Free API key (100 calls/mo) | ⚠️ PROBE (needs signup) |
| THETIS-MRV | EU ship CO₂/fuel/efficiency per ship-year | Free, login for bulk | ⚠️ PROBE — no existing table targets it |
| Paris MoU | PSC inspections/detentions (Europe) → `vessel_safety` | Free, manual account request for bulk XML | ⚠️ PROBE — the real `vessel_safety` path |

## API Key Setup (new — from Session 16b sweep)

| Task | Details | Status |
|------|---------|--------|
| Register FRED API key | Free at fredaccount.stlouisfed.org — needed as `oil_prices` backup (WTI/Brent) | ⛔ Not started |
| Register FreightPulse key | Free plan, 100 calls/mo, no credit card — probe per-route container rates for `freight_rates` | ⛔ Not started |
| Request Paris MoU data account | Manual form → bulk XML for `vessel_safety` | ⛔ Not started |

> **"Collector built" is not evidence a source works.** All five rows above were
> written against endpoints that had never been called successfully. Each returns
> 0 rows, and because `collect_data()` swallows its own exceptions and returns 0,
> the orchestrator reports them as `[OK]`. Verified 2026-07-30.

---

*Last updated: 2026-08-09 (Session 16b — new-source sweep)*
