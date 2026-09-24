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
| `freight_rates` | 0 | OilPriceAPI indices (BDI/BCI/SCFI/WCI) wired weekly, but plan-gated — expect 0 until a paid key. Container per-route rates still need a source. **NYFI collector added (`nyfi`, weekly) — awaiting free NYSHEX account key (`NYSHEX_API_KEY`).** |

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

## New collectors (Session 17, 2026-08-24)

| Collector | What | Status |
|-----------|------|--------|
| `unlocode` (`unlocode_ports`, ondemand) | Downloads the UNECE UN/LOCODE release zip (~13.5 MB), parses the 3 comma-delimited CSV parts (12 positional columns, no header; country header lines skipped), converts DDMM[N/S] DDDMM[E/W] coordinates to decimal degrees, and loads **all** rows into `ports` keyed by `unlocode` PK (`INSERT OR REPLACE` overwrites the Digitraffic-derived subset). `function_class` (class '1' = port) and `status` are preserved per row via migration `202608240001`. | ✅ BUILT — zip re-verified live 2026-08-24 (ranged GET 206, 13.5 MB). |
| `nyfi` (`nyfi`, weekly) | NYSHEX NYFI container-freight index → `freight_rates`. Auth `Authorization: ApiKey <key>` (env `NYSHEX_API_KEY`, free account). Parser written defensively against the documented schema (timeframe YYYY-WW, publishDate, indices[] with lane/value/unit); raw sample logged at DEBUG. | ✅ BUILT — endpoint live (401 without key, as expected). Awaiting free NYSHEX account key. |

## New collectors (Session 18, 2026-08-26)

| Collector | What | Status |
|-----------|------|--------|
| `freightpulse` (`freightpulse`, daily) | FreightPulse port congestion — no auth required, GET `/api/v1/port-congestion` returns 114 global ports with congestion index, vessel counts, wait times, berth utilization, container dwell days, trend analysis. Snapshot date from `data.timestamp` (ISO8601). New `port_congestion` table partitioned by `(partition_date, source)`, dedup keys `(snapshot_date, port_code, source)`. Migration `202608260001`. | ✅ BUILT — 20 tests, 366/366 suite pass. |
| `aisstream` (`aisstream`, daily) — verified | Already built in prior session. WebSocket at `wss://stream.aisstream.io/v0/stream`, 4 chokepoint bounding boxes, 120s collection window, writes to `ais_positions` + `vessels`. API key in `.env`. | ✅ BUILT + WIRED — first live run pending. |

## Data source vetting (Session 18, 2026-08-26)

| Source | Verdict | Notes |
|--------|---------|-------|
| USACE Waterborne Commerce Statistics | ❌ NO-GO | navigationdatacenter.us behind login wall; USACE digital library PDFs only; navdata-test API transport error |
| AISstream.io | ✅ GO | Free API key, WebSocket, global real-time AIS. Collector already built. |
| FreightPulse Port Congestion | ✅ GO — BUILT | Free REST API, no auth, 114 ports. Collector built 2026-08-26. |
| FreightPulse Freight Rates | ⏳ TO PROBE | `/api/v1/freight-rates` — could fill the container per-route rate gap in `freight_rates` |
| FreightPulse Fuel Prices | ⏳ TO PROBE | `/api/v1/fuel-prices` — bunker fuel (VLSFO/HSFO) by region |
| FreightPulse Disruptions | ⏳ TO PROBE | `/api/v1/disruptions` — active supply chain disruptions |

## New working sources (Session 16b sweep, 2026-08-09)
| Source | What | Auth | Status |
|--------|------|------|--------|
| UN/LOCODE official (UNECE) | Canonical global port/location reference, 116K rows | None | ✅ GO — verified live. **BUILT 2026-08-24** (`src/collectors/unlocode.py`, wired as `unlocode_ports`); replaces Digitraffic-derived `ports`. |
| NOAA ERDDAP | Oceanographic gridded/tabular data (SST, wave height, currents, WW3) | None | ✅ GO — verified live. Complements Open-Meteo for `marine_weather`. |
| FRED | Oil benchmarks (WTI/Brent) — backup for `oil_prices` | Free API key | ✅ GO (needs signup) |
| FreightPulse | Per-route container rates (Shanghai→LA 40ft) + port congestion | Free API key (100 calls/mo) | ⚠️ PROBE (needs signup) |
| THETIS-MRV | EU ship CO₂/fuel/efficiency per ship-year | Free, login for bulk | ⚠️ PROBE — no existing table targets it |
| Paris MoU | PSC inspections/detentions (Europe) → `vessel_safety` | Free, manual account request for bulk XML | ⚠️ PROBE — the real `vessel_safety` path |

## API Key Setup (new — from Session 16b sweep)

| Task | Details | Status |
|------|---------|--------|
| Register FRED API key | Free at fredaccount.stlouisfed.org — needed as `oil_prices` backup (WTI/Brent). Collector `fred_oil.py` built ahead of the key (Session 23) — SKIPs cleanly until it lands, then needs one live run to verify the parser against a real response. | ⛔ Not started |
| Register FreightPulse key | Free plan, 100 calls/mo, no credit card. Current 5 collectors send no auth header at all — code needs updating to wire `FREIGHTPULSE_API_KEY` in once registered. Also still blocked on the HTTPS outage, see [[freightpulse-tls-nogo]]/Session 19 below. User registering directly. | ⏳ In progress (user) |
| Register NYSHEX key (`NYSHEX_API_KEY`) | Free account for NYFI container-freight index → `freight_rates`. `nyfi` collector already built and wired (Session 17), just needs the key. User applied, awaiting response. | ⏳ Pending (awaiting NYSHEX response) |
| Request Paris MoU data account | Manual form → bulk XML for `vessel_safety` | ⛔ Not started |
| Register US Census API key (`CENSUS_API_KEY`) | Free, instant signup at `api.census.gov/data/key_signup.html` — needed for `api.census.gov/data/timeseries/intltrade` as a US-side `trade_flow` supplement/fallback to UN Comtrade (see DATA_SOURCES.md 5.3). Collector `census_trade.py` built ahead of the key (Session 22) — SKIPs cleanly until it lands, then needs one live run to verify the parser against a real response. User registering directly. | ⏳ In progress (user) |

> **"Collector built" is not evidence a source works.** All five rows above were
> written against endpoints that had never been called successfully. Each returns
> 0 rows, and because `collect_data()` swallows its own exceptions and returns 0,
> the orchestrator reports them as `[OK]`. Verified 2026-07-30.

## Session 19 (2026-08-27) — CI fix, HF restructure, FreightPulse TLS NO-GO

| Task | Details | Status |
|------|---------|--------|
| Fix CI lint blocker | `tests/collectors/test_freightpulse.py` had 3 mid-file imports (E402) + 2 duplicate imports (F811), added in Session 18. Blocked the last 2 daily `Collect Data` GH Actions runs at the lint step, before collection ever ran. Consolidated all imports to the top of the file. | ✅ Fixed — ruff + full 424-test suite clean |
| Re-run EIA weekly stocks | Not a bug — the collector was simply never invoked while CI sat lint-broken. Live-verified 2026-08-27: 194 rows written to `oil_inventories`. | ✅ Confirmed working |
| `.env` audit | `UN_COMTRADE_API_KEY`, `GFW_API_TOKEN`, `VESSELAPI_API_KEY`, `SHIPLOOKUP_API_KEY` are all present as **empty** lines in `.env` (not filled in), not broken collectors. None of the four feed `vessel_registry` or `vessel_safety` (GFW/VesselAPI/ShipLookup write to `vessels`/`port_calls`, which already have data via other sources). | ⛔ Needs free signups if wanted |
| FreightPulse HTTPS — **NO-GO for now, not a phantom source** | `freightpulsehq.com:443` returns TLS alert 112 (`unrecognized_name`) for **every** SNI tested (the real hostname, `www.`, a bogus name, and no SNI at all) — confirmed 3 ways: curl, Python `requests`, raw `openssl s_client` direct to the IP. This means the server currently has **no TLS vhost configured at all**, not a client/network issue. Plain HTTP (port 80) to the same host works fine and returns real JSON (`{"success":false,"error":"Missing required parameter..."}`) — this is a genuine, live "FreightPulse - Logistics Intelligence API" product, unlike the `wp-json` phantom endpoints in [[phantom-endpoint-collectors]]. The 5 freightpulse* collectors (rates/fuel/disruptions/carriers/congestion) are correctly written and correctly call `https://`; they'll start working the moment FreightPulse's HTTPS comes back. Don't downgrade to `http://` to work around it. Re-probe periodically rather than treating this as dead. | ❌ NO-GO (transient, re-check later) |
| HF upload restructured to per-table folders | `upload_huggingface.py` was flattening all 16 tables to `<table>.parquet` at repo root. Confirmed via HF's `datasets-server` API that this collapses the whole dataset into a single `default`/`train` config — `load_dataset()` can't address individual tables, and the README's own `ds["air_cargo"]` usage example didn't actually work. Rewrote `export_tables()` to write `<table>/<table>.parquet` (matches financial-data-pipeline's convention), added `delete_patterns=["*.parquet"]` to the upload so old flat root files get removed on next push, and fixed the README usage example. | ✅ Code fixed + dry-run verified locally; **not yet pushed to the live HF repo** |
| USCG PSIX (`cgmix.uscg.mil`) | **NO-GO, probed 2026-08-28.** The base site and its human search page (`PSIXVesselSearch.aspx`) load fine (200), but the `.asmx` SOAP endpoint (`PSIXData.asmx`, with or without `?WSDL`, GET or POST, with/without a warmed session cookie, with a full browser UA+Referer) always 302s to `/PSIX/ValidationError.aspx` — and so does the site's own `robots.txt`. That combination (blocking `.asmx` *and* `robots.txt` specifically, while a nonsense path 200s normally) is a WAF/URL-rewrite rule targeting known recon/scanner request patterns, not a bot-UA check — headers/cookies/method didn't change the outcome. Per the vetting rule, a blocked source is a NO-GO, not a challenge; not attempting further circumvention. Only remaining surface is the human search form, which is scraping (not an API) and likely sits behind the same WAF. `vessel_registry`/`vessel_safety` are still open gaps. | ❌ NO-GO |

## Session 20 (2026-08-28) — new source sweep: trade_flow alternates + vessel_registry/safety

| Source | Target | Verdict | Notes |
|--------|--------|---------|-------|
| US Census International Trade API (`api.census.gov/data/timeseries/intltrade`) | `trade_flow` (US-side alternate/supplement to UN Comtrade) | ✅ GO — needs free key | Live-probed: now returns `{"Missing Key"}` on a real query — Census tightened keyless access at some point (used to allow low-volume unauthenticated calls). Keys are free and instant at `api.census.gov/data/key_signup.html`, standard federal API. Not yet built. |
| Eurostat Comext SDMX (`ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1`) | `trade_flow` (EU-side alternate/supplement) | ✅ GO — BUILT (Session 21) | See Session 21 below. |
| WTO Timeseries API (`api.wto.org/timeseries/v1`) | `trade_flow` | ⚠️ PROBE, low priority | Needs an Azure-APIM-style subscription key (401 without one); couldn't confirm a genuinely free tier from the portal page (JS SPA, no plain-text pricing found). Lower priority than Census/Eurostat/Comtrade, which already give global + US + EU coverage. |
| Equasis (`equasis.org`) | `vessel_registry`/`vessel_safety` | Unchanged — still NO-GO for bulk | Re-checked; still a per-vessel lookup tool (needs login + a specific IMO list), no bulk export or API found in the public pages. Matches the existing verdict in [[phantom-endpoint-collectors]]. |
| ITU MARS ship station database (`itu.int/mars`) | `vessel_registry` (MMSI/callsign reference) | ⚠️ PROBE, inconclusive | Base URL and search both redirect (301/302), likely to a login or a JS search UI. Didn't find a bulk/keyless path in a quick probe; not investigated further to avoid a rabbit hole. |

No new GO for `vessel_registry`/`vessel_safety` this round — Paris MoU (manual account) and THETIS-MRV (portal login) remain the best existing leads for those two.

## Session 21 (2026-08-28) — Eurostat Comext collector built

| Task | Details | Status |
|------|---------|--------|
| Build `eurostat_comext.py` | `collect_comext_data(reporter=..., partner="", flows=("1","2"))` — annual freq, product=TOTAL, partner wildcarded per call (both import+export = 2 requests). Parses SDMX-generic XML with stdlib `xml.etree.ElementTree` (no new dependency). Remaps Comext's `CXT_EU_FLUX` flow codes (1/2/3) to Comtrade's M/X/RX letters so both sources share `trade_flow.flow_code`. | ✅ Built |
| Widen `trade_flow` schema | Migration `202608280001`: `reporter_code`/`partner_code` INTEGER→VARCHAR (Comext uses ISO-alpha like `"DE"`, Comtrade uses numeric UN M49 like `156` — can't share an INTEGER column), plus a new `currency` column (`DEFAULT 'USD'`) since Comext reports EUR, not USD like the `trade_value_usd` column name implies. Zero-data-risk — `trade_flow` had 0 rows (UN Comtrade key never registered). `schema.py`'s `TRADE_FLOW` raw SQL updated to match so a fresh DB build gets the same shape. | ✅ Done |
| Wire into orchestrator | `collect_all.py`: `eurostat_comext`, `collect_comext_data(reporter="DE")`, monthly, no key required — mirrors `un_comtrade`'s one-reporter wiring style (`reporter_code=156` for China). | ✅ Wired |
| Tests | 6 new tests (`test_eurostat_comext.py`): XML parsing (multi-series, import vs export flow mapping, empty, invalid-XML), and `collect_comext_data` with mocked fetch/write. Also fixed `test_un_comtrade_to_db` in `test_integration.py`, which asserted `reporter_code == 156` (int) — now correctly `"156"` (str) post-widen. | ✅ 430/430 pass, ruff clean |
| Live verification | `collect_comext_data(reporter="DE")` against the real API wrote **2,439 rows** — 253 partner countries, 2021-2025, both flows. Spot-checked DE↔US: export 2022 €155.9B / 2023 €157.7B, import 2022 €70.2B / 2023 €72.0B — matches known real trade volumes. `currency` correctly `'EUR'` for every row. | ✅ Live-verified |

`trade_flow` now has a working keyless source. Census remains the other GO, pending your key.

## Session 22 (2026-08-28) — Census trade collector built ahead of the key

| Task | Details | Status |
|------|---------|--------|
| Confirm real field names before building | `variables.json` for both `exports/hs` and `imports/hs` is itself keyless — fetched both to get real field names rather than guess: exports use `E_COMMODITY`/`ALL_VAL_MO`, imports use `I_COMMODITY`/`GEN_VAL_MO` (Census's "General Imports, Total Value" measure — imports don't have an `ALL_VAL_MO` equivalent). Cross-checked against the API's own published example query (`exports/hs?get=E_COMMODITY_SDESC,CTY_NAME,ALL_VAL_YR,DIST_NAME&time=2013-01&CTY_CODE=1220`), which also confirms the key requirement is real ("All data queries ... now require an API key"). | ✅ Confirmed via keyless metadata |
| Build `census_trade.py` | `collect_trade_data(period=..., comm_lvl="HS2", flows=("X","M"))` — one request per flow, HS2-level aggregation, defaults to 2 months before today (Census intltrade's typical release lag). Response parser follows Census's standard list-of-lists shape (header row first), used across every Bureau API. | ✅ Built |
| Add `CENSUS_API_KEY` to config | `src/config.py`: `census_api_key` field + env read, matching the existing per-source key pattern. | ✅ Done |
| Wire into orchestrator | `collect_all.py`: `census_trade`, monthly, `requires_key="census_api_key"` — SKIPs cleanly (confirmed via `get_collectors()`) until the key is set, same as every other key-gated collector in this repo. | ✅ Wired |
| Tests | 9 new tests (`test_census_trade.py`): export/import parsing, malformed-value rows dropped not crashed, missing-key `ValueError`, invalid `flow_code`, `collect_trade_data` with mocked fetch/write, default-period computation. | ✅ 439/439 pass, ruff clean |
| **Not live-verified** | `CENSUS_API_KEY` is not yet registered, so **only the metadata (field names, endpoint paths, response shape) is confirmed live** — the actual authenticated JSON body has never been seen. Same caution as the phantom-endpoint lesson in [[phantom-endpoint-collectors]]: "collector built" is not "collector works." Run `collect_trade_data()` for real the first time the key lands, before trusting its output. | ⚠️ Needs first live run once keyed |

## Session 23 (2026-08-28) — FRED oil price collector built ahead of the key

| Task | Details | Status |
|------|---------|--------|
| Confirm real series IDs before building | FRED requires a key for `series/observations`, but a keyless request 400s with a real error message ("Variable api_key is not set") rather than 404ing, confirming the endpoint route is real. `DCOILWTICO` (WTI) and `DCOILBRENTEU` (Brent) both resolve to real public series pages, checked keylessly. | ✅ Confirmed via keyless probes |
| Build `fred_oil.py` | `collect_oil_prices(start_date=..., end_date=...)` — fetches both series, **merges them into one row per date** before writing (same pattern as `oilpriceapi.py`'s `_parse_oil_prices`): `oil_prices` dedups on `(price_date, source)`, so two separate single-column writes for the same date/source would each overwrite the other's row. Handles FRED's `"."` missing-observation marker. Defaults to the last 30 days. | ✅ Built |
| Add `FRED_API_KEY` to config | `src/config.py`: `fred_api_key` field + env read. | ✅ Done |
| Wire into orchestrator | `collect_all.py`: `fred_oil`, daily, `requires_key="fred_api_key"` — SKIPs cleanly (confirmed via `get_collectors()`, 41 collectors registered) until the key is set. | ✅ Wired |
| Tests | 6 new tests (`test_fred_oil.py`): multi-series merge, unknown series ignored, missing-marker (`"."`) dropped without producing an all-null row, missing-key `ValueError`, `collect_oil_prices` with mocked fetch/write. | ✅ 445/445 pass, ruff clean |
| **Not live-verified** | `FRED_API_KEY` is not yet registered — only the endpoint route and series existence are confirmed live, not the authenticated response body. FRED's JSON shape has been stable and documented for over a decade, but verify against a real response the first time a key is available, same caution as `census_trade.py`. | ⚠️ Needs first live run once keyed |

## Session 24 (2026-08-28) — vessel_registry/vessel_safety source hunt, round 2

| Source | Target | Verdict | Notes |
|--------|--------|---------|-------|
| IMO GISIS (`gisis.imo.org/Public/`) | `vessel_registry` | NO-GO for bulk | Even the "Public" module redirects to a login page (`Login.aspx`) for every path tried, including the ships search. Same shape as Equasis: a per-vessel lookup tool behind a login, not a bulk registry. Didn't create an account to test further (same reasoning as the standing decision not to do account creation on the user's behalf). |
| Tokyo MoU APCIS (`apcis.tmou.org`) | `vessel_safety` | ⚠️ PROBE, real but not confirmed working | Upgraded from the old "web only, guessed URLs 404" verdict — the real PSC database is a live jQuery/AJAX SPA (`tokyo-mou.org/inspections-detentions/psc-database/` → `apcis.tmou.org/public/`) with a genuine search endpoint (`POST ?action=getships`, params include `imo`/`flag`/`authority`/`class`/`From`/`Till`/`result`) found in the app's own JS. Live-tested: a plain POST (with and without a warmed session cookie) returns `<script>window.location.reload();</script>` instead of results — the app needs a multi-step session bootstrap (likely a `getTabByLinkUid` call first) that isn't documented anywhere. Even once working, responses are HTML fragments (`$(...).html(data)`), not JSON — this would be scraping a session-gated SPA, not calling a clean API. **Stopped here to avoid a reverse-engineering rabbit hole** — real effort to build, and HTML-scraping is inherently more fragile than the JSON/XML/SDMX sources built so far. Worth a dedicated session if `vessel_safety` becomes a priority. |
| data.gov CKAN API (`catalog.data.gov/api/3/action/*`) | general search | Inconclusive | The classic CKAN `package_search` endpoint now 404s with a generic (non-CKAN) "Not Found" body — looks like data.gov's API surface changed since this route was last known to work. Not pursued further; the site itself is live (200) so a newer API may exist under a different path. |

No new GO this round either. Confirms the pattern from Session 20: `vessel_registry`/`vessel_safety` are genuinely hard gaps — every free source found so far is either login-gated for per-vessel lookups only (Equasis, GISIS), requires a manual account request for bulk (Paris MoU, THETIS-MRV), or is a session-gated SPA with no documented API (Tokyo MoU APCIS). None are outright dead (unlike the wp-json phantom endpoints), they're just genuinely friction-heavy — worth revisiting with more time budget rather than expecting a quick keyless win.

---

## Session 25 (2026-09-17) — VLCC rate gap confirmed, Baltic Exchange trial probed

| Item | Finding | Verdict |
|------|---------|---------|
| VLCC prices currently tracked? | No. `hormuz_monitor.py` parses `vlcc_td3c_ws` / `vlcc_td3c_tce_usd_day` into `oil_prices`, and is registered in `collect_all.py` (`requires_key="hormuz_api_key"`), but the collector is dormant — no free tier exists for Hormuz Monitor (confirmed NO-GO, already logged at line 58 above), so it's skipped on every run, not failing. `tankermap.py` only gives live vessel positions/cargo tonnage, not rates. `oilpriceapi.py`'s `collect_freight_indices` covers BDI/BCI/SCFI/WCI, not VLCC/tanker indices. | Confirmed gap |
| Baltic Exchange free trial (`balticexchange.com/en/free-trial.html`) | Site sits behind a JS proof-of-work bot wall — `curl`/scripted fetch gets a "Challenge Validation" stub even with a browser UA; only renders via a real browser. Trial is **1 week**, self-serve signup form, covers all published wet/dry/gas indices including **BDTI** (Baltic Dirty Tanker Index — carries VLCC/dirty-tanker routes like TD3C). Delivered **via website + mobile app**; signup page does not mention API/programmatic access as part of the trial — the API (`api.balticexchange.com`) is a separate product, so the trial may be dashboard-only, not a key a collector can hit. Gate: requires a **work email on a company domain** ("emails from non-company domains will be queried") — a personal Gmail address likely doesn't pass self-serve. | **PROBE** — real trial, tanker data included, but API access and the personal-email gate are unconfirmed without a human completing signup |

**Next step if pursuing Baltic Exchange**: sign up manually (company email needed) and check whether the trial account grants `api.balticexchange.com` credentials or only web/app viewing — that determines whether it's usable by a collector at all before spending build time on `src/collectors/baltic_exchange.py`.

## Session 26 (2026-09-23) — CI unblocked, HF as store of record, PortWatch port data

| Task | Details | Status |
|------|---------|--------|
| Fix CI mypy blocker | 13 `union-attr`/`arg-type` errors in `nyfi`, `freightpulse`, `freightpulse_carriers`, `eurostat_comext` failed the type-check step on every daily run from 2026-08-26 → 09-23, so nothing was collected for four weeks. | ✅ Fixed |
| HF as store of record | CI started each run from an empty DB and published only that run's rows: snapshot tables never accumulated and a failed source vanished from HF. New `seed_from_huggingface.py` step loads the published tables first; `upload_huggingface.py` no longer deletes remote parquet. Added a `concurrency` group so two runs can't race on the publish. | ✅ Done |
| PortWatch port data | 4 new tables: `port_activity`, `port_profiles`, `trade_nowcast`, `disruption_events` — see DATA_SOURCES.md 8.3. | ✅ Built, live-verified locally |
| FreightPulse API changed | HTTPS is back, but `port-congestion` now requires a port/country/region, `freight-rates` requires US origin/destination zips (trucking, not ocean), and `carriers` requires a search term — all 422 on the bulk call. Those 3 collectors are unregistered in `collect_all.py` so they stop failing the daily run. `fuel-prices` and `disruptions` still work. **2026-09-24: retired, not redesigned** — congestion is now PortWatch port calls (rebuilt from `port_activity` as `port_congestion_proxy`), rates and carriers are US trucking only. See DATA_SOURCES.md 3.7. | ✅ Retired |
| Watch HF growth | Snapshot tables now accumulate. `ais_positions` (Axiomancer ~59K rows/day) is the fastest grower — decide whether to keep full daily history or thin it. | ⏳ Decision |

## Follow-ups / TODO

- [ ] User to provide a YouTube video link; extract information from it (via transcript, since video/audio can't be watched directly) and fold findings into the relevant pipeline docs.

---

*Last updated: 2026-08-28 (Session 24 — vessel_registry/safety source hunt round 2, no new GO, Tokyo MoU APCIS found but session-gated)*
