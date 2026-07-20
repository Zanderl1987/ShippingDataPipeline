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
| **Verdict** | **GO** — Free tier viable for live tracking. Must persist to Parquet on receipt. |

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
| **Verdict** | **GO** — 10+ years of historical AIS, vessel identity with registry cross-reference, event detection built in. Free token available. |

### 1.6 BarentsWatch (Norway) `barentswatch.no`

| Field | Detail |
|-------|--------|
| **Data** | Norwegian coastal waters AIS — real-time vessel positions, identities |
| **Access** | **Open data** — free, no registration for basic access |
| **Auth** | None (basic), API key (extended) |
| **Depth** | Real-time only |
| **Verdict** | **GO** — Valuable for North Sea / Norwegian Sea coverage. Regional but high quality. |

### 1.7 NOAA MarineCadastre (US) `marinecadastre.gov`

| Field | Detail |
|-------|--------|
| **Data** | US coastal waters AIS — historical, bulk-distributed |
| **Access** | **Free, public domain** |
| **Auth** | None |
| **Depth** | Multi-year historical, delayed by ~3-6 months |
| **Format** | Shapefile, GeoPackage, CSV |
| **Verdict** | **GO** — Best source for US waters historical AIS. Bulk download, not API. Good for backfill + training. |

### 1.8 Danish Maritime Authority `dma.dk`

| Field | Detail |
|-------|--------|
| **Data** | European waters AIS — daily files |
| **Access** | **Open data, free** |
| **Auth** | None |
| **Depth** | Historical (rolling window), daily files |
| **Verdict** | **GO** — European waters coverage. Daily file delivery, free. |

---

## 2. Port Call Data

### 2.1 VesselAPI Port Events `vesselapi.com`

| Field | Detail |
|-------|--------|
| **Data** | Port arrivals/departures, vessel port history, inbound vessels with ETA |
| **Access** | **Free tier** (no credit card), Paid for higher volume |
| **Auth** | Bearer token (free registration) |
| **Rate Limit** | Free tier: not strictly documented but reasonable |
| **Depth** | Rolling window (free), extended history (paid) |
| **Format** | REST JSON |
| **Endpoints** | `/portevents`, `/portevents/port/{unlocode}`, `/portevents/vessel/{id}`, `/port/{unlocode}/inbound` |
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
| **Verdict** | **GO** — Free tier, credit system, search is free. Good supplement to Seafarer Index. |

### 4.3 VesselAPI Vessels `vesselapi.com`

| Field | Detail |
|-------|--------|
| **Data** | Full vessel information — name, type, dimensions, flag, builder, owner, manager, class society, engine specs, IMO/MMSI |
| **Access** | **Free tier** (no credit card) |
| **Auth** | Bearer token |
| **Rate Limit** | Free: reasonable usage |
| **Search** | By IMO, MMSI, name, callsign, flag, vessel type, year built, owner |
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
| **Verdict** | **GO** — The canonical source for trade flow data. Free tier adequate for research/analysis. 100K records per call is generous. |

### 5.2 World Bank WITS `wits.worldbank.org`

| Field | Detail |
|-------|--------|
| **Data** | Trade statistics, tariff data, and non-tariff measures |
| **Access** | **Free with registration** |
| **Verdict** | **PROBE** — Partially overlaps with UN Comtrade. Use if Comtrade doesn't cover a specific need. |

---

## 6. Weather & Environmental (Ancillary)

### 6.1 Open-Meteo `open-meteo.com`

| Field | Detail |
|-------|--------|
| **Data** | Free weather forecasts + historical — wind, waves, currents, temperature, precipitation |
| **Access** | **Free, no auth** — Open source |
| **Rate Limit** | 10,000 requests/day (free) |
| **Depth** | Historical from 1940 (ERA5 reanalysis) |
| **Verdict** | **GO** — Essential ancillary data for route analysis, delay correlation. No auth. |

### 6.2 Port of Barcelona Weather API

See 2.5 — includes real-time weather, 5-day forecast, alerts, currents/tides for Barcelona port.

---

## 7. Aggregated / Derived Datasets

### 7.1 Neptune AIS (Python Library) `github.com/xang1234/neptune`

| Field | Detail |
|-------|--------|
| **What** | Python library that normalizes 6+ AIS sources (NOAA, DMA, GFW, AISHub, AISStream, Digitraffic Finland) into a single schema. Detects port calls, EEZ crossings, encounters, loitering. Polars-native + DuckDB SQL. |
| **Access** | **Free, open source** — MIT license |
| **Pip** | `pip install neptune-ais` |
| **Verdict** | **USE** — Not a data source, but a tool to ingest many of the sources above. Worth evaluating as middleware. |

---

## Prioritized Integration Order

### Phase 1 (Quick wins — minimal auth, free, rich data)

| Priority | Source | Why first |
|----------|--------|-----------|
| 1 | **Axiomancer Overwatch** | Free, no auth, instant AIS positions. Zero setup cost for development. |
| 2 | **OpenAIS** | Free, no auth, **historical backfill** from 2021. Track reconstruction. |
| 3 | **Seafarer Index** | Free, CC BY 4.0 vessel registry + port reference. Enrichment layer. |
| 4 | **Open-Meteo** | Free weather data for route/delay analysis. |

### Phase 2 (Registration required, free tier)

| Priority | Source | Why here |
|----------|--------|----------|
| 5 | **Global Fishing Watch** | Free token, 10+ years AIS, vessel identity, event detection. Rich but needs registration. |
| 6 | **VesselAPI Port Events** | Free tier, port call data + vessel lookup in one API. |
| 7 | **UN Comtrade** | Free API key, trade flow volumes. Registration + key needed. |
| 8 | **ShipLookup API** | Free 1K credits/month vessel registry. |

### Phase 3 (Streaming / specialized)

| Priority | Source | Why here |
|----------|--------|----------|
| 9 | **AISStream** | Live WebSocket streaming. Needs running process + persistence. |
| 10 | **BarentsWatch** | Norwegian waters — regional but high quality. |
| 11 | **NOAA MarineCadastre** | US waters historical bulk download. |
| 12 | **Danish Maritime Authority** | European waters daily files. |

### Phase 4 (Analysis data)

| Priority | Source | Why here |
|----------|--------|----------|
| 13 | **FBX / Baltic Exchange** | Freight rate data for market analysis. Depends on trial/scrape outcome. |
| 14 | **SCFI** | Weekly Shanghai container rates. Public, free, low frequency. |
| 15 | **Port of Barcelona** | Open port operations API — reference model for port data. |
| 16 | **Singapore OCEANS-X** | Transshipment hub data. Register to evaluate. |

---

## Dead Ends (NO-GO, recorded to avoid re-visiting)

| Source | Blocker |
|--------|---------|
| IMO GISIS | No API — web only. Not machine-accessible. |
| HVCC Hamburg | B2B contract required. Not viable for Phase 1-3. |
| VT Explorer | Paid credit model. Defer. |
| MarineTraffic APIs (free) | Most endpoints behind paid tier. Free tier too restrictive. |

---

*Vetted: 2026-07-20. Re-verify any source before building — APIs change, free tiers get removed, and keys expire.*
