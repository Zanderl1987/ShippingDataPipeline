# Shipping Data Sources — Vetted Catalog

## How to Read This

Each source is categorized by data type and rated across the axes that matter for pipeline integration:

| Axis | Meaning |
|------|---------|
| **Access** | Free / Free-tier / Paid / Open-data |
| **Auth** | None / API key / Token / Registration / Contract |
| **Rate Limit** | What limits apply per key/IP/user |
| **Depth** | How far back data is available |
| **Verdict** | GO (integrate) / PROBE (test first) / NO-GO (blocked) |

---

## 1. AIS Vessel Tracking — Real-time

### 1.1 AISStream `aisstream.io`

| Field | Detail |
|-------|--------|
| **Data** | Real-time global terrestrial AIS via WebSocket — vessel positions, MMSI, speed, course, heading, nav status, destination, ETA, port calls |
| **Access** | Free-tier (rate-limited), Paid for higher volume |
| **Auth** | API key (free registration) |
| **Rate Limit** | Free: ~10 messages/sec (varies); docs state "reasonable use" |
| **Depth** | No historical — live stream only (must persist to store) |
| **Docs** | https://aisstream.io/documentation |
| **SDK** | OpenAPI 3.0 models + Python lib: https://github.com/aisstream/ais-message-models |
| **Collector** | `src/collectors/aisstream.py` — collect_stream (daily, 120s window) → `ais_positions` + `vessels` |
| **Verdict** | **GO — BUILT** — collector built and wired, API key in `.env`. First live run pending (Session 18). |

### 1.2 AISHub `aishub.net`

| Field | Detail |
|-------|--------|
| **Data** | Global terrestrial AIS (pooled from contributors) — positions, vessel details |
| **Access** | Free (must contribute your own AIS feed), Paid for API-only |
| **Auth** | API key |
| **Rate Limit** | Members: ~1 request/minute |
| **Depth** | Current snapshot only |
| **Format** | JSON, XML, CSV |
| **Verdict** | **PROBE** — The contribution requirement is friction. API-only paid tier may be acceptable, but 1 req/min is very restrictive. |

### 1.3 Axiomancer Overwatch `axiomancer.io`

| Field | Detail |
|-------|--------|
| **Data** | Vessel registry + real-time AIS positions (global snapshot ~18K vessels), per-vessel lookup by IMO, port-area positions |
| **Access** | **Free, no auth** — CC BY 4.0 license |
| **Auth** | None (public API, CORS-open) |
| **Rate Limit** | 60 req/min IP, 1,000 req/UTC day IP |
| **Depth** | Current snapshot only (latest position per vessel) |
| **Format** | JSON, GeoJSON |
| **Special** | `GET /api/v1/positions/latest` — global snapshot (~800KB gzipped), CDN-cached 5 min. No key needed. |
| **Verdict** | **GO** — Best free, no-auth AIS source found. Ideal for initial development. License allows reuse with attribution. |

### 1.4 OpenAIS `open-ais.org`

| Field | Detail |
|-------|--------|
| **Data** | Global AIS positions, vessel details, voyage reports, trajectories, heatmaps |
| **Access** | **Free, open data** — PostGIS-based REST API |
| **Auth** | None |
| **Rate Limit** | Not documented — reasonable use |
| **Depth** | Historical from **Feb 2021** onward |
| **Format** | OGC-compliant, CQL filters, GeoJSON |
| **Special** | Offers continuous-aggregated positions (30-min buckets), daily trajectory linestrings, and heatmap grids. Historical backfill possible. |
| **Verdict** | **GO** — Free, no auth, **historical backfill**. Rare combination. Excellent for building track history. |

### 1.5 Global Fishing Watch `globalfishingwatch.org`

| Field | Detail |
|-------|--------|
| **Data** | AIS-based fishing effort (2012+), vessel presence, SAR detections, vessel identity + registry, port visits, encounters, loitering events |
| **Access** | **Free with API token** (registration required) |
| **Auth** | Bearer token (free registration) |
| **Rate Limit** | Free tier: reasonable (not strictly documented); paid for higher volume |
| **Depth** | From **2012 to ~96 hours ago** |
| **Format** | JSON, GeoJSON, 4Wings tiled maps |
| **SDK** | V3 API — vessel search, events, map visualization |
| **License** | CC BY 4.0 for derived data |
| **Collector** | `src/collectors/global_fishing_watch.py` — 6 functions: search, get vessel, get events, get port visits, get fishing events, get loitering events |
| **Verdict** | **GO** — 10+ years of historical AIS, vessel identity with registry cross-reference, event detection built in. Free token available. |

### 1.6 BarentsWatch (Norway) `barentswatch.no`

| Field | Detail |
|-------|--------|
| **Data** | Norwegian coastal waters AIS — real-time vessel positions, identities |
| **Access** | **Open data** — free, registration required for API access |
| **Auth** | Bearer token (free registration) |
| **Rate Limit** | Not documented — reasonable use |
| **Depth** | Real-time only (14-day window) |
| **Limitations** | Norwegian economic zone only. Fishing vessels <15m and leisure craft <45m excluded. Data older than 14 days unavailable. |
| **Collector** | `src/collectors/barentswatch.py` — get_positions, get_vessel_track, get_vessels_in_area |
| **Verdict** | **GO** — Valuable for North Sea / Norwegian Sea coverage. Regional but high quality. |

### 1.7 NOAA MarineCadastre (US) `marinecadastre.gov`

| Field | Detail |
|-------|--------|
| **Data** | US coastal waters AIS — historical, bulk-distributed |
| **Access** | **Free, public domain** |
| **Auth** | None |
| **Depth** | Multi-year historical, delayed by ~3-6 months |
| **Format** | GeoParquet (2023+), Shapefile, GeoPackage, CSV |
| **Collector** | `src/collectors/noaa_marinecadastre.py` — get_available_years, download_year, parse_parquet_chunk |
| **Verdict** | **GO** — Best source for US waters historical AIS. Bulk download, not API. Good for backfill + training. |

### 1.8 Danish Maritime Authority `dma.dk`

| Field | Detail |
|-------|--------|
| **Data** | European waters AIS — daily files |
| **Access** | **Open data, free** |
| **Auth** | None |
| **Depth** | Historical (rolling window), daily files |
| **Verdict** | **GO** — European waters coverage. Daily file delivery, free. |

### 1.9 Digitraffic (Baltic Sea AIS) `meri.digitraffic.fi`

| Field | Detail |
|-------|--------|
| **Data** | Baltic Sea AIS — live positions, vessel metadata, Finnish port calls; global port/location reference (SSN locations) |
| **Access** | **Open data, free** (Fintraffic / Finnish Transport Infrastructure Agency) |
| **Auth** | None |
| **Rate Limit** | None documented; reasonable use |
| **Depth** | Live snapshot only (no long historical) |
| **Docs** | https://meri.digitraffic.fi/swagger/ (OpenAPI 3.0) |
| **Endpoints** | `/api/ais/v1/locations` (GeoJSON, ~1,020 live positions), `/api/ais/v1/vessels` (~890 vessels), `/api/port-call/v1/port-calls` (~200 Finnish port calls), `/api/port-call/v1/ports` (18,660 global SSN locations, 12,256 with coordinates) |
| **Collector** | `src/collectors/digitraffic.py` — collect_locations, collect_vessels, collect_port_calls, collect_ports |
| **Tables** | `ais_positions`, `vessels`, `port_calls`, `ports` |
| **Verdict** | **GO** — verified live 2026-08-09. No auth, open swagger. |

---

## 2. Port Call Data

### 2.1 VesselAPI Port Events `vesselapi.com`

| Field | Detail |
|-------|--------|
| **Data** | Port arrivals/departures, vessel port history, inbound vessels with ETA |
| **Access** | **Free tier** (no credit card), Paid for higher volume |
| **Auth** | Bearer token (free registration) |
| **Rate Limit** | Free tier: 150 calls/month |
| **Depth** | Rolling window (free), extended history (paid) |
| **Format** | REST JSON |
| **Endpoints** | `/portevents`, `/portevents/port/{unlocode}`, `/portevents/vessel/{id}`, `/port/{unlocode}/inbound` |
| **Collector** | `src/collectors/vesselapi.py` — get_port_events, get_vessel_port_events, get_inbound_vessels, search_vessel |
| **Verdict** | **GO** — Free tier available, comprehensive port events API. Good for current port operations. |

### 2.2 Data Docked `datadocked.com`

| Field | Detail |
|-------|--------|
| **Data** | Port calls, vessel positions, route planning, weather |
| **Access** | **Free trial available** |
| **Auth** | API key |
| **Rate Limit** | Free trial: 15 requests/minute |
| **Depth** | 30 days historical on free trial |
| **Verdict** | **PROBE** — Evaluate free trial coverage vs VesselAPI. Redundant if VesselAPI covers needs. |

### 2.3 HVCC Hamburg `hvcc-hamburg.de`

| Field | Detail |
|-------|--------|
| **Data** | Hamburg container terminal sailing list — real-time arrival/departure times, berth info, ETA/ETD/ATA/ATD, previous/next ports, voyage numbers |
| **Access** | **Paid** (B2B contract) |
| **Auth** | HTTP Basic Auth (credentials per partner) |
| **Rate Limit** | 2 requests/minute (production) |
| **Depth** | Up to 3 weeks planning horizon |
| **Format** | REST JSON, OpenAPI spec |
| **Verdict** | **NO-GO** (for now) — Contract gated. Keep as future option for deep Hamburg coverage. |

### 2.4 Port of Rotterdam `portal.api.portofrotterdam.com`

| Field | Detail |
|-------|--------|
| **Data** | Port operations APIs — AIS, port call data, infrastructure |
| **Access** | **Free guest account** available |
| **Auth** | Port of Rotterdam guest account |
| **Verdict** | **PROBE** — Guest account required; assess scope and limits. Excellent if accessible. |

### 2.5 Port of Barcelona OpenInfoAPI `github.com/portdebarcelona/OpenInfoAPI-Ports`

| Field | Detail |
|-------|--------|
| **Data** | Ships arrivals/departures/forecasts, vessels in port, ferry/cruise schedules, weather at port, port infrastructure, traffic statistics, economic data |
| **Access** | **Free, open API** |
| **Auth** | None |
| **Format** | REST JSON |
| **Verdict** | **GO** — Open, no auth, covers Barcelona operations. Also serves as a reference pattern — other ports may adopt similar specs. |

### 2.6 MPA Singapore OCEANS-X `oceans-x.mpa.gov.sg`

| Field | Detail |
|-------|--------|
| **Data** | Singapore port — vessel arrivals/departures, vessel information, vessel tracking |
| **Access** | **API marketplace platform** — free registration |
| **Auth** | Platform account |
| **Verdict** | **PROBE** — Singapore is the world's largest transshipment hub. Platform-based access; need to register and evaluate. |

### 2.7 VT Explorer `vtexplorer.com`

| Field | Detail |
|-------|--------|
| **Data** | Port calls (arrivals/departures) by port, vessel, or fleet — includes AIS position + voyage data with each event |
| **Access** | **Paid** (credit-based via API key) |
| **Auth** | API key (`userkey`) |
| **Verdict** | **NO-GO** (for now) — Paid credit model. Keep as future option. |

---

## 3. Freight Rate Indices

### 3.1 Freightos Baltic Index (FBX) `fbx.freightos.com`

| Field | Detail |
|-------|--------|
| **Data** | Container freight spot rates — 12 major tradelanes (Trans-Pacific, Asia-Europe, Transatlantic), 20GP/40GP/40HC container types |
| **Access** | **Public web** — free index page with daily rates; CSV download for subscribers |
| **Auth** | Free web access (no login for public charts/numbers), CSV export requires registration |
| **Depth** | Historical from 2016 |
| **Format** | Web page, CSV export, public REST-like API endpoints on the FBX site |
| **Notes** | The FBX public page at fbx.freightos.com displays current rate for each route. Web scraping the public page is possible (no login wall), but scraping ToS should be reviewed. |
| **Verdict** | **PROBE** — Publicly visible data. Scrape feasibility depends on ToS. The Baltic Exchange offers a free 1-week trial of their full data API — that may be the cleaner path. |

### 3.2 Baltic Exchange API `balticexchange.com`

| Field | Detail |
|-------|--------|
| **Data** | Complete suite — dry bulk, tanker, gas, container (FBX), air freight indices, forward curves, OPEX indices, fixture lists, market reports |
| **Access** | **Paid subscription** (trial: 1 week free) |
| **Auth** | API key (ICE Data API / Baltic portal) |
| **Depth** | Varies by index; some from 2016+ |
| **Verdict** | **PROBE** — 1-week free trial. Use trial to understand data shape, then decide if subscription is justified for container + dry bulk rates. |

### 3.3 Shanghai Containerized Freight Index (SCFI)

| Field | Detail |
|-------|--------|
| **Data** | Weekly spot rates for export from Shanghai to 15 global ports (port-to-port + surcharges) |
| **Access** | **Public** — published weekly by Shanghai Shipping Exchange |
| **Auth** | None (public reports) |
| **Depth** | From 2005 |
| **Format** | Published reports, survey-based |
| **Verdict** | **GO** — Public, free. Weekly frequency only. |

### 3.4 Container Shipping Rates Scraper (Apify) `apify.com/crawlerbros/container-shipping-rates-scraper`

| Field | Detail |
|-------|--------|
| **Data** | FBX route rates (same as 3.1) — structured JSON via Apify actor |
| **Access** | **Pay-per-run** on Apify (free tier with limited credits) |
| **Auth** | Apify API token |
| **Format** | Structured JSON — route codes, origin/destination, rate in USD, container type, published date |
| **Verdict** | **PROBE** — Convenient wrapper around FBX data, but costs per run. Only worth it if FBX scraping is ToS-prohibited. |

### 3.5 FRED (St. Louis Fed) `fred.stlouisfed.org`

| Field | Detail |
|-------|--------|
| **Data** | 800K+ US/global economic series. Freight-relevant series are mostly **US domestic** (trucking PPI, Cass Freight Index, rail intermodal) — **no Baltic Dry / container ocean-rate series** (verified 2026-08-09: `baltic`, `container freight rate`, `shipping index` searches return only NASDAQ Baltic stock indices, rail/truck PPIs, shipbuilding IPIs). Has WTI/Brent oil (backup for OilPriceAPI) and LNG. |
| **Access** | **Free with API key** (no credit card) |
| **Auth** | `api_key` query param (free registration at fredaccount.stlouisfed.org) |
| **Rate Limit** | Generous (120 calls/min recommended cap) |
| **Depth** | Varies; many series decades long |
| **Verdict** | **GO for oil benchmarks (backup)** — but **NOT for `freight_rates`**; it carries no ocean container/Baltic dry-bulk rates. Useful redundancy for `oil_prices` (WTI/Brent) at zero marginal cost once the free key is registered. |

### 3.6 SeaRates Freight Index API `docs.searates.com`

| Field | Detail |
|-------|--------|
| **Data** | Container freight index per trade lane (e.g. Shanghai-Rotterdam), FCL/LCL, wet/dry bulk per-mt/m3 |
| **Access** | **Paid** (marketing page: "request a quote") |
| **Auth** | API key |
| **Verdict** | **NO-GO** — `freight-index.searates.com` does not resolve (no DNS, verified 2026-08-09) and the API is commercial. |

### 3.7 FreightPulse `freightpulsehq.com`

| Field | Detail |
|-------|--------|
| **Data** | Real-time ocean container route rates (e.g. Shanghai→LA 40ft), port congestion index, US diesel, trucking rates |
| **Access** | **Free plan — 100 API calls/month, all endpoints, no credit card** |
| **Auth** | `X-API-Key` (not required for basic calls — unauthenticated GET returns full data) |
| **Rate Limit** | Free: 100 calls/month |
| **Depth** | Free: current values only (paid unlocks history) |
| **Endpoints** | `GET /api/v1/port-congestion` (114 ports, congestion index + vessel counts + dwell days + trend), `/api/v1/freight-rates`, `/api/v1/fuel-prices`, `/api/v1/disruptions`, `/api/v1/carriers` |
| **Collector** | `src/collectors/freightpulse.py` — collect_port_congestion (daily) → `port_congestion` |
| **Verdict** | **GO — BUILT** — port congestion collector built 2026-08-26 (20 tests, 366/366 suite pass). No auth required for the basic call. Freight rates + fuel prices + disruptions still to probe. |

### 3.8 NYSHEX NYFI `nyshex.com`

| Field | Detail |
|-------|--------|
| **Data** | New York Container Index (NYFI) — weekly container-freight composite by trade lane/sub-index |
| **Access** | **Free account** (registration required) |
| **Auth** | API key — header `Authorization: ApiKey <key>` (env `NYSHEX_API_KEY`) |
| **Depth** | Published weekly readings (startDate/endDate ISO8601 window) |
| **Format** | REST JSON — timeframe (YYYY-WW), publishDate, indices[] per lane |
| **Collector** | `src/collectors/nyfi.py` — collect_nyfi (weekly) → `freight_rates` (source `nyfi`) |
| **Verdict** | **GO** — endpoint verified live 2026-08-24 (401 without key, as expected). Collector built; awaiting a free NYSHEX account key to validate the actual response shape. |

---

## 4. Vessel Registry / Identity

### 4.1 Seafarer Index `seafarerindex.com/data`

| Field | Detail |
|-------|--------|
| **Data** | Ships (~537KB JSON) — IMO-keyed with type, flag, dimensions, year built, owner/manager links. Ports (~43.5MB JSON) — UN/LOCODE-keyed with name, country, coordinates, timezone |
| **Access** | **Free, open data** — CC BY 4.0, CORS-open |
| **Auth** | None |
| **Update** | Static dumps, regenerated daily |
| **Format** | JSON |
| **Verdict** | **GO** — Free, no auth, permissively licensed. Perfect for vessel reference/enrichment. |

### 4.2 ShipLookup API `shiplookup.com`

| Field | Detail |
|-------|--------|
| **Data** | Vessel specs — IMO, MMSI, name, callsign, length, beam, gross tonnage, year built, ship type, flag |
| **Access** | **Free tier** (1,000 credits/month, no credit card) |
| **Auth** | API key (`X-API-Key` header) |
| **Rate Limit** | Free: 10 requests/minute |
| **Search** | Search by IMO, MMSI, name, or callsign (search endpoint is free, no credits consumed) |
| **Collector** | `src/collectors/shiplookup.py` — search_vessels (free), get_vessel_by_imo (1 credit), get_vessel_by_mmsi (1 credit), get_vessel_by_name (1 credit) |
| **Verdict** | **GO** — Free tier, credit system, search is free. Good supplement to Seafarer Index. |

### 4.3 VesselAPI Vessels `vesselapi.com`

| Field | Detail |
|-------|--------|
| **Data** | Full vessel information — name, type, dimensions, flag, builder, owner, manager, class society, engine specs, IMO/MMSI |
| **Access** | **Free tier** (no credit card) |
| **Auth** | Bearer token |
| **Rate Limit** | Free: 150 calls/month |
| **Search** | By IMO, MMSI, name, callsign, flag, vessel type, year built, owner |
| **Collector** | `src/collectors/vesselapi.py` — search_vessel, get_vessel_details |
| **Verdict** | **GO** — Combines vessel registry + position + port events in one API. Free tier available. |

### 4.4 IMO GISIS `gisis.imo.org`

| Field | Detail |
|-------|--------|
| **Data** | Official IMO ship registry — vessel particulars, company particulars |
| **Access** | **Free, public** |
| **Auth** | None (public web interface) |
| **Format** | Web only — no public API (would require scraping) |
| **Verdict** | **NO-GO** — No API. Web-only. The authoritative source, but not machine-accessible. Use Seafarer Index or ShipLookup instead. |

### 4.5 Equasis `equasis.org`

| Field | Detail |
|-------|--------|
| **Data** | Ship safety and quality information — vessel details, inspection records, casualty data, ownership |
| **Access** | **Free with registration** |
| **Auth** | Username/password |
| **Format** | Web + CSV export |
| **Verdict** | **PROBE** — Free but requires registration. Good for safety/quality data enrichment; evaluate if CSV export is automated. |

### 4.6 UN/LOCODE official (UNECE) `unece.org`

| Field | Detail |
|-------|--------|
| **Data** | The **canonical** global code for ports and other trade/transport locations — 103K+ locations, 249 countries, UN/LOCODE, coordinates, subdivision, function classifier, status |
| **Access** | **Free, open data** — ODC-PDDL (public domain) |
| **Auth** | None |
| **Depth** | Full code list, released biannually (2025-1 is current) |
| **Format** | CSV (3 parts), TXT, MS Access `.mdb`, XML, TTL |
| **Download** | `https://opensource.unicc.org/un/unece/uncefact/vocab-locode/-/jobs/artifacts/2025-1/download?job=package-release` (zip, ~13.5 MB; verified HTTP 200 2026-08-09). Legacy mirrors at `service.unece.org/trade/locode/loc242csv.zip` return 403. |
| **Coordinates** | DDMM format (`4230N 00131E`) — parse to decimal degrees |
| **Verdict** | **GO — BUILT** — verified live 2026-08-09 (re-verified 2026-08-24: ranged GET 206, 13.5 MB), 116,533 rows across the 3 CSV parts. **Collector built 2026-08-24** (`src/collectors/unlocode.py`, wired as `unlocode_ports`); loads the full code list into `ports` keyed by `unlocode` PK, overwriting the Digitraffic-derived subset. Note: the release CSVs are **comma-delimited** (12 positional columns, no header row) despite the legacy pipe-delimited UNECE convention. This is the authoritative replacement for the Digitraffic `ssnLocations`-derived `ports` subset. |

### 4.7 EU Fleet Register `vessel-register.oceans-and-fisheries.ec.europa.eu`

| Field | Detail |
|-------|--------|
| **Data** | All EU-flagged fishing vessels — CFR, UVI/IMO, IRCS, flag, GT, LOA, power, latest event |
| **Access** | **Free** |
| **Auth** | None (web search) but results are captcha-gated; export button present |
| **Format** | Web UI with "Export Data"; no clean REST API found |
| **Verdict** | **NO-GO** (for merchant fleet) — this register covers **fishing** vessels only, so it doesn't fit `vessel_registry` (which targets commercial ships). Captcha on results is a scraping blocker. Keep as niche fishing-fleet reference only. |

### 4.8 THETIS-MRV `mrv.emsa.europa.eu` (EMSA ship emissions)

| Field | Detail |
|-------|--------|
| **Data** | Verified CO₂, CH₄, N₂O emissions + fuel consumption + efficiency metrics per ship/year (EU MRV regulation), ~87K records 2018-2024 |
| **Access** | **Free, public** (official EMSA portal; login for bulk) |
| **Auth** | Portal login required for the Excel downloads |
| **Community API** | `https://thetis-mrv-api.vercel.app` — docs load (FastAPI) but data endpoints currently 500 (broken backend, verified 2026-08-09) |
| **Verdict** | **PROBE** — official data is a genuinely valuable emissions source for `vessel_registry`-adjacent analytics, but the free path is manual (portal login → per-year Excel). No existing table targets it; needs a schema decision before building. |

### 4.9 Paris MoU Port State Control `parismou.org`

| Field | Detail |
|-------|--------|
| **Data** | PSC inspection results for the Paris MoU region (Europe + North Atlantic) — inspections, detentions, bannings, deficiencies, KPIs, risk profiles |
| **Access** | **Free, public web** — but **bulk XML requires an account request** (manual form: Paris MoU Data Request Application Form → login → automated download) |
| **Auth** | Account (manual request, free) |
| **Format** | Web UI (JS SPA, no public JSON API — verified 2026-08-09, `parismou.org/inspection-search` is a Drupal SPA with no `/jsonapi`), bulk XML with account |
| **Verdict** | **PROBE** — the manual account request is friction, but this is the **authoritative** `vessel_safety` dataset for European waters. Bulk XML (once granted) is the right integration path; the SPA has no API to scrape. |
| **Alt API** | `mou.mrl.dev` — community PSC aggregation API (`/api/inspections`, `/api/detentions`, covers Paris/Tokyo/Indian Ocean MoU + USCG). **Free but requires HTTP Basic credentials** — 401 without them (verified 2026-08-09). One-off community project; availability uncertain. |

### 4.10 Tokyo MoU PSC `tokyo-mou.org`

| Field | Detail |
|-------|--------|
| **Data** | PSC inspection/detention data for Asia-Pacific region |
| **Access** | **Free, public web** |
| **Auth** | None (web) |
| **Format** | Web only; search pages 404 under the guessed URLs (verified 2026-08-09) |
| **Verdict** | **PROBE** — inspect the actual search UI path before ruling out; no API confirmed. Lower priority than Paris MoU (which is already PROBE). |

---

## 5. Trade Flow Data

### 5.1 UN Comtrade API `comtradedeveloper.un.org`

| Field | Detail |
|-------|--------|
| **Data** | Global merchandise trade data — import/export volumes by HS code, country, year/month. Billions of records covering 200+ countries. |
| **Access** | **Free tier** (API key), Premium for high-volume |
| **Auth** | API subscription key (free registration) |
| **Rate Limit** | Free: 500 API calls/day, 100K records per call |
| **Depth** | From 1962 (varies by reporter country) |
| **Format** | REST JSON, bulk CSV/Parquet files |
| **SDK** | Official Python package: `comtradeapicall` (pypi) |
| **Collector** | `src/collectors/un_comtrade.py` — get_trade_flow, get_commodity_trade, get_bilateral_trade |
| **Verdict** | **GO** — The canonical source for trade flow data. Free tier adequate for research/analysis. 100K records per call is generous. |

### 5.2 World Bank WITS `wits.worldbank.org`

| Field | Detail |
|-------|--------|
| **Data** | Trade statistics, tariff data, and non-tariff measures |
| **Access** | **Free with registration** |
| **Verdict** | **PROBE** — Partially overlaps with UN Comtrade. Use if Comtrade doesn't cover a specific need. |

### 5.3 US Census International Trade API `api.census.gov/data/timeseries/intltrade`

| Field | Detail |
|-------|--------|
| **Data** | US import/export trade by HS code, country, port, month — official US Census Bureau foreign trade statistics |
| **Access** | **Free**, key required (was keyless for low volume historically; probed 2026-08-28 and it now returns `"Missing Key"` on every query) |
| **Auth** | Free, instant API key at `api.census.gov/data/key_signup.html` |
| **Format** | REST JSON |
| **Verdict** | **GO** — needs the free key registered, not yet built. Good US-side supplement/fallback to UN Comtrade for `trade_flow`. |

### 5.4 Eurostat Comext (international trade in goods) `ec.europa.eu/eurostat/api/comext/dissemination/sdmx/2.1`

| Field | Detail |
|-------|--------|
| **Data** | EU trade in goods since 1988, HS2/HS4/HS6/CN8 detail (dataset `DS-045409`) |
| **Access** | **Free, no auth** |
| **Format** | SDMX 2.1 (XML) |
| **Verdict** | **PROBE, leaning GO** — live-verified 2026-08-28: `/dataflow` and `/dataflow/ESTAT/DS-045409` both return real metadata with no key. A data query needs the exact dimension order from the DSD (`.../datastructure/ESTAT/DS-045409`) — a guessed key (`M.DE.US.TOTAL.VALUE_IN_EUROS`) 400'd with `INVALID_QUERY_NB_FILTERS`. Same codebase already talks to Eurostat (`eurostat_maritime.py`), so the client pattern exists — this is a per-dataset key-shape research task, not a new integration pattern. |

### 5.5 WTO Timeseries API `api.wto.org/timeseries/v1`

| Field | Detail |
|-------|--------|
| **Data** | WTO merchandise/services trade statistics |
| **Access** | Subscription key required (401 without one) |
| **Verdict** | **PROBE, low priority** — couldn't confirm a genuinely free tier from the API portal (JS SPA, no plain-text pricing found in a quick probe 2026-08-28). Lower priority than Comtrade/Census/Comext, which already cover global + US + EU. |

---

## 6. Weather & Environmental (Ancillary)

### 6.1 Open-Meteo `open-meteo.com`

| Field | Detail |
|-------|--------|
| **Data** | Free weather forecasts + historical — wind, waves, currents, temperature, precipitation |
| **Access** | **Free, no auth** — Open source |
| **Rate Limit** | 10,000 requests/day (free) |
| **Depth** | Historical from 1940 (ERA5 reanalysis) |
| **Endpoints** | `api.open-meteo.com/v1/forecast` (weather), `marine-api.open-meteo.com/v1/marine` (waves/currents) |
| **Collector** | `src/collectors/open_meteo.py` — collect_marine, collect_weather |
| **Tables** | `marine_weather`, `weather` |
| **Verdict** | **GO** — Essential ancillary data for route analysis, delay correlation. No auth. Both collectors wired daily at Singapore (1.264, 103.82). |

### 6.3 NOAA ERDDAP `coastwatch.pfeg.noaa.gov/erddap`

| Field | Detail |
|-------|--------|
| **Data** | Thousands of gridded/tabular oceanographic datasets — significant wave height, sea surface temperature, currents, wind, sea ice (GFS/CFSR/WW3/AVISO feeds) |
| **Access** | **Free, no auth** |
| **Auth** | None |
| **Rate Limit** | Reasonable use |
| **Depth** | Varies by dataset; many multi-decade |
| **Format** | JSON, CSV, NetCDF, GeoJSON (REST `tabledap`/`griddap`) |
| **Verdict** | **GO** (ancillary) — verified live 2026-08-09 (`coastwatch.pfeg.noaa.gov/erddap/info/index.json` + wave-height search return JSON). Strong complement to Open-Meteo for `marine_weather` (SST, actual current vectors, WW3 wave fields) at chokepoint/port coordinates. No auth, so zero signup friction. |

### 6.4 Copernicus Marine Service `data.marine.copernicus.eu`

| Field | Detail |
|-------|--------|
| **Data** | CMEMS — global ocean analyses/forecasts: waves, currents, SST, sea ice, sea level |
| **Access** | **Free with registration** (motu API + FTP/HTTP downloads) |
| **Auth** | Free account |
| **Verdict** | **PROBE** — richer/more authoritative than Open-Meteo marine, but heavier (NetCDF, registration, quota per product). Worth it only if Open-Meteo marine depth proves insufficient. Low priority given Open-Meteo + NOAA ERDDAP already cover the table. |

### 6.2 Port of Barcelona Weather API

See 2.5 — includes real-time weather, 5-day forecast, alerts, currents/tides for Barcelona port.

---

## 7. Maritime Risk & Chokepoints

### 7.1 Eagle Intelligence `eagleintelmari.com`

| Field | Detail |
|-------|--------|
| **Data** | Live risk status for 6 maritime chokepoints — Hormuz, Suez, Bab el-Mandeb, Panama, Malacca, Bosphorus. Status tiers (SEVERE/ELEVATED/MONITORING), signal counts, HIGH-severity headlines, crisis-day counters. |
| **Access** | **Free** — no auth required |
| **Auth** | None (attribution required: CC BY 4.0) |
| **Rate Limit** | 1 req/min fair use |
| **Endpoints** | `/api/chokepoint-status` (all 6), `/api/hormuz-status` (Hormuz only), `/alerts.xml` (RSS HIGH alerts) |
| **Depth** | Real-time only (no historical API) |
| **Docs** | https://eagleintelmari.com/developers |
| **Verdict** | **GO** — Zero-friction risk data. Adds geopolitical risk layer to analytics. |

---

## 8. Oil & Energy Data

### 8.1 EIA Petroleum `eia.gov/opendata`

| Field | Detail |
|-------|--------|
| **Data** | US petroleum — weekly crude oil stocks, refinery utilization, imports by country of origin, product supply |
| **Access** | **Free with API key** (registration required) |
| **Auth** | API key (`api_key` query parameter) |
| **Rate Limit** | 5,000 requests/hour per key |
| **Depth** | Historical from 1920s (varies by series) |
| **Format** | REST JSON (JSON API 2.0) |
| **Endpoints** | `/v2/petroleum/pri/spt/data/` (stocks), `/v2/petroleum/pri/sup/data/` (supply), `/v2/petroleum/import/pnt/data/` (imports) |
| **Collector** | `src/collectors/eia_petroleum.py` — get_weekly_stocks, get_weekly_supply, get_monthly_imports_by_country, get_refinery_utilization |
| **Table** | `oil_inventories` |
| **Verdict** | **GO** — Authoritative US petroleum data. Free API key, generous rate limits. Essential for US crude inventory analysis. |

### 8.2 JODI-Oil `data.jodiol.org`

| Field | Detail |
|-------|--------|
| **Data** | Global oil data — production, consumption, imports, exports, stocks for 90+ countries. 13 products (crude, NGL, gasoline, diesel, jet fuel, etc.), 14 flow categories |
| **Access** | **Free, no auth** — bulk CSV download |
| **Auth** | None |
| **Rate Limit** | No API rate limit (CSV download) |
| **Depth** | Monthly from **2002** (varies by country/product) |
| **Format** | CSV bulk download |
| **Products** | CRUDEOIL, NGL, GASOLINE, GASDIES, JETKERO, FUELOIL, KEROSEN, NAPHTHA, LPG, OTHER, REFINERY, TOTAL |
| **Flows** | INDPROD (production), TOTIMPSB (imports), TOTEXPSB (exports), TOTDEMO (consumption), INDTRNF (transfers) |
| **Collector** | `src/collectors/jodi_oil.py` — get_primary_data, get_secondary_data, collect_primary, collect_secondary |
| **Table** | `oil_trade` |
| **Notes** | Data is 1-2 months behind. KTONS units (convert to barrels: crude × 7.37, products × 7.40) |
| **Verdict** | **GO** — Best free source for global oil production and trade. Monthly frequency, 20+ year history. |

### 8.3 IMF PortWatch `portwatch.imf.org`

| Field | Detail |
|-------|--------|
| **Data** | Daily chokepoint transit counts — vessel numbers by type (container, tanker, bulk, general cargo, RoRo) + capacity (DWT) for 7 major chokepoints |
| **Access** | **Free, no auth** — ArcGIS REST API |
| **Auth** | None |
| **Rate Limit** | No documented limit (pagination required for >1000 features) |
| **Depth** | Daily from **2019** |
| **Format** | ArcGIS Feature Service (JSON) |
| **Chokepoints** | Hormuz, Suez, Bab el-Mandeb, Panama, Malacca, Turkish Straits, Cape of Good Hope |
| **Collector** | `src/collectors/imf_portwatch.py` — get_chokepoint_info, get_daily_chokepoint_data, collect_chokepoint_transits |
| **Table** | `chokepoint_transits` |
| **Verdict** | **GO** — Unique daily chokepoint transit data. No auth, 5+ year history. Essential for supply chain risk analysis. |

### 8.4 TankerMap `tankermap.com`

| Field | Detail |
|-------|--------|
| **Data** | Live tanker positions — vessel name, type, flag, destination, ETA, speed, cargo estimates (barrels). Port calls at oil terminals with arrival/departure times |
| **Access** | **Free, no auth** — JSON API |
| **Auth** | None |
| **Rate Limit** | No documented limit |
| **Depth** | Real-time only (live positions + recent port calls) |
| **Format** | JSON |
| **Endpoints** | `/api/vessels` (tanker positions), `/api/portcalls` (oil terminal port calls) |
| **Cargo Estimates** | Tanker type → tonnes (VLCC 200K, Suezmax 120K, Aframax 80K, Handysize 35K). Converted to barrels: crude × 7.37, products × 7.40 |
| **Collector** | `src/collectors/tankermap.py` — get_live_vessels, get_port_calls, collect_vessels, collect_port_calls |
| **Tables** | `ais_positions` (tanker positions), `port_calls` (oil terminal calls) |
| **Verdict** | **GO** — Free tanker tracking with cargo estimates. Unique oil-specific vessel data. |

### 8.5 Hormuz Monitor `hormuzmonitor.com`

| Field | Detail |
|-------|--------|
| **Data** | Composite risk index (0-10) for Strait of Hormuz. Oil prices (Brent/WTI/Dubai, 15-min delay). VLCC rates (WS index + TCE USD/day). LNG JKM prices. Traffic volume |
| **Access** | **Paid** — registration shows no free tier available |
| **Auth** | API key (paid registration) |
| **Verdict** | **NO-GO** — free tier claimed but registration yields only paid plans. Collector preserved for future use if access changes. |

### 8.6 OilPriceAPI `oilpriceapi.com`

| Field | Detail |
|-------|--------|
| **Data** | Oil price benchmarks (BRENT, WTI, DUBAI, Urals, OPEC basket, natural gas, coal, carbon, FX). Plan-gated freight indices (Baltic Dry/Capesize, SCFI, Drewry WCI) |
| **Access** | **Free-tier** — 200 requests/month after 7-day 10,000-req trial |
| **Auth** | API key — header `Authorization: Token <key>` (env `OILPRICEAPI_KEY`) |
| **Rate Limit** | Free: 200 req/month (120 req/hour on demo) |
| **Depth** | Latest values (source-timestamped); historical behind paid plan |
| **Format** | REST JSON |
| **Endpoints** | `/v1/prices/latest?by_code=CODE`, `/v1/prices/past_week`, `/v1/prices/marine-fuels`, `/v1/prices/futures` |
| **Collector** | `src/collectors/oilpriceapi.py` — collect_oil_prices (daily), collect_freight_indices (weekly) |
| **Tables** | `oil_prices` (brent/wti/dubai), `freight_rates` (indices when available) |
| **Docs** | https://docs.oilpriceapi.com |
| **Verdict** | **GO for oil benchmarks** — verified live 2026-08-09, BRENT/WTI/DUBAI map 1:1 to `oil_prices`. Freight indices are plan-gated on free keys; tolerated, may yield 0 rows. |

---

## 9. Aggregated / Derived Datasets

### 9.1 Neptune AIS (Python Library) `github.com/xang1234/neptune`

| Field | Detail |
|-------|--------|
| **What** | Python library that normalizes 6+ AIS sources (NOAA, DMA, GFW, AISHub, AISStream, Digitraffic Finland) into a single schema. Detects port calls, EEZ crossings, encounters, loitering. Polars-native + DuckDB SQL. |
| **Access** | **Free, open source** — MIT license |
| **Pip** | `pip install neptune-ais` |
| **Verdict** | **USE** — Not a data source, but a tool to ingest many of the sources above. Worth evaluating as middleware. |

---

## Prioritized Integration Order

### Phase 1 ✓ Complete (Quick wins — minimal auth, free, rich data)

| Priority | Source | Status |
|----------|--------|--------|
| 1 | **Axiomancer Overwatch** | ✓ Integrated |
| 2 | **OpenAIS** | Deferred (self-hosted) |
| 3 | **Seafarer Index** | ✓ Integrated |
| 4 | **Open-Meteo** | ✓ Integrated |

### Phase 2 ✓ Complete (Registration required, free tier)

| Priority | Source | Status |
|----------|--------|--------|
| 5 | **Global Fishing Watch** | ✓ Integrated |
| 6 | **VesselAPI Port Events** | ✓ Integrated |
| 7 | **UN Comtrade** | ✓ Integrated |
| 8 | **ShipLookup API** | ✓ Integrated |

### Phase 3 ✓ Complete (Streaming / specialized)

| Priority | Source | Status |
|----------|--------|--------|
| 9 | **AISStream** | ✓ Integrated |
| 10 | **BarentsWatch** | ✓ Integrated |
| 11 | **NOAA MarineCadastre** | ✓ Integrated |
| 12 | **Danish Maritime Authority** | Deferred |

### Phase 4 ✓ Complete (Analysis data — oil & risk)

| Priority | Source | Status |
|----------|--------|--------|
| 13 | **Eagle Intelligence** | ✓ Integrated |
| 14 | **EIA Petroleum** | ✓ Integrated |
| 15 | **JODI-Oil** | ✓ Integrated |
| 16 | **IMF PortWatch** | ✓ Integrated |
| 17 | **TankerMap** | ✓ Integrated |
| 18 | **Hormuz Monitor** | ✓ Integrated |

### Phase 5 (Freight rates & dashboards)

| Priority | Source | Status |
|----------|--------|--------|
| 19 | **FBX / Baltic Exchange** | Pending (trial/scrape) |
| 20 | **SCFI** | Pending |
| 21 | **Port of Barcelona** | Pending |
| 22 | **Singapore OCEANS-X** | Pending |

---

## Dead Ends (NO-GO, recorded to avoid re-visiting)

| Source | Blocker |
|--------|---------|
| IMO GISIS | No API — web only. Not machine-accessible. |
| HVCC Hamburg | B2B contract required. Not viable for Phase 1-3. |
| VT Explorer | Paid credit model. Defer. |
| MarineTraffic APIs (free) | Most endpoints behind paid tier. Free tier too restrictive. |
| Sinay.ai | 401 without API key, registration required. |
| FreightPulse | ~~Returns HTML landing page, API may not be live.~~ **RECOVERED 2026-08-26** — port congestion API is live and returns full JSON without auth. Moved to Section 3.7 as BUILT. |
| emissions.dev | 401 without valid key, needs registration. |
| Hormuz Monitor | Listed as having free tier but registration yields only paid plans. Collector kept for future use. |
| Danish Maritime Authority AIS (`web.ais.dk/aisdata/`) | SSL cert hostname mismatch; `verify=False` → `RemoteDisconnected`. Probed 2026-08-09. |
| OECD maritime CO₂ emissions | Landing page 403; SDMX `GetData/MARITIME_CO2` endpoints 404. Probed 2026-08-09. |
| ICC IMB Piracy Reporting Centre | Piracy reports map-gated, no public API. Probed 2026-08-09. |
| NGI LNG Daily | Paid subscription, no free tier. Probed 2026-08-09. |
| Signal Ocean | `api.signal-ocean.com` does not resolve (no DNS). Probed 2026-08-09. |
| UP Indices (UPI) | Freemium — API requires a paid plan. Probed 2026-08-09. |
| Digitraffic `ports` (1.9) | Previously unwired (a stale comment in `collect_all.py` blamed an HTTP 502 from 2026-08-03). **Recovered 2026-08-09** — endpoint returns 200; `ssnLocations` gives 12,256 ports with coordinates. Wired as `digitraffic_ports`. |
| Open-Meteo `weather` (6.1) | Previously 0 rows though `collect_weather` was written. **Recovered 2026-08-09** — `collect_weather` was never registered in `get_collectors()` (orphaned-collector pattern, like aisstream). Wired as `open_meteo_weather`. |
| OEC API (Observatory of Economic Complexity) | `api.oec.world` returns **403 Forbidden** on all versions (v2/v3/tesseract) regardless of UA/Accept headers. Probed 2026-08-09. NO-GO as a `trade_flow` alternative to UN Comtrade. |
| SeaRates Freight Index | `freight-index.searates.com` does not resolve (no DNS). API is commercial anyway. Probed 2026-08-09. |
| Straits.live Hormuz API | `straits.live/api` returns 403 Forbidden (WAF). Probed 2026-08-09. |
| THETIS-MRV community API (`thetis-mrv-api.vercel.app`) | Docs load (200) but all data endpoints return **500** (broken Supabase backend). Probed 2026-08-09. |
| `mou.mrl.dev` PSC API | 401 Unauthorized without HTTP Basic credentials; one-off community project. Probed 2026-08-09. |
| Paris MoU inspection search | JS SPA, no `/jsonapi` or public JSON API; bulk requires a manual data-request account. Probed 2026-08-09. |
| Tokyo MoU PSC search | Guessed URLs return 404; no API confirmed. Probed 2026-08-09. |
| EU Fleet Register | Fishing vessels only (not merchant `vessel_registry`); captcha-gated results. Probed 2026-08-09. |
| FRED for `freight_rates` | Free API key, but carries **no** ocean container/Baltic dry-bulk rates — only US domestic trucking/rail indices. Verified 2026-08-09. GO only as an `oil_prices` backup (WTI/Brent). |

---

*Vetted: 2026-07-20. Updated: 2026-08-09 (Session 16 — added Digitraffic + OilPriceAPI, 6 new dead ends, recovered ports + weather wiring; Session 16b — new-source sweep: UN/LOCODE official, NOAA ERDDAP, FRED, FreightPulse, THETIS-MRV, Paris MoU, 10 new dead ends). Re-verify any source before building — APIs change, free tiers get removed, and keys expire.*
