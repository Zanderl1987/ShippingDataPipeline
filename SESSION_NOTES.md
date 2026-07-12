# Shipping & Trade Data Pipeline — Session Notes

## Date: 2026-07-11

## Goal

Add shipping, import/export, and trade data to the financial data pipeline. Focus on the most granular data available — individual shipment/bill of lading level with port of loading/discharge and component-level product classification.

---

## Free Data Sources — Ingestion Candidates

### Tier 1: Shipment-Level (Most Granular)

| Source | Data | Access | Granularity | Notes |
|--------|------|--------|-------------|-------|
| **ImportYeti API** | US CBP BoL data | Free API (credits) | HS6, ports, companies, vessel, TEU, weight | `data.importyeti.com/v1.0/` |
| **OEC BoL Premium** | US CBL data (2000–2026) | ~$50/mo | HS6, ports, TEU, weight, CIF | `oec.world/en/resources/bulk-download/bill-of-lading-oec` |
| **US ITC DataWeb** | US trade statistics | Free web/API | HTS10 (10-digit for US) | `dataweb.usitc.gov/` |
| **CBP FOIA** | Raw ACE manifests | FOIA request | Everything on the BoL form | Submit via `FOIA.gov` or CBP SecureRelease portal |

### Tier 2: Country-Level (Bilateral Flows)

| Source | Data | Access | Granularity | Notes |
|--------|------|--------|-------------|-------|
| **UN Comtrade** | 200 countries, 1962+ | Free API (500/day, 100K records/call) | HS6, bilateral, monthly | `comtradeplus.un.org/` — Python package `comtradr` on CRAN |
| **WTO TTD** | Tariffs + trade flows | Free CSV/web | MTN categories, time series | `ttd.wto.org/en` |
| **WITS** | Aggregated trade + tariffs | Free web | HS6, tariff analysis | `wits.worldbank.org/` — aggregates Comtrade + TRAINS + WTO IDB |

### Tier 3: Maritime Intelligence

| Source | Data | Access | Granularity | Notes |
|--------|------|--------|-------------|-------|
| **Global Fishing Watch** | AIS vessel presence 2012+ | Free API | Hourly positions, vessel types | 4Wings API, dataset `public-global-presence:latest` |
| **US CGMIX/PSIX** | US vessel documentation | Free web search | Vessel specs, ownership, documentation | `cgmix.uscg.mil/PSIX/PSIXSearch.aspx` |
| **NOAA/BOEM AIS** | US vessel traffic (AIS) | Free bulk download | Position, speed, course data | `marinecadastre.gov/ais/` |
| **Marine Cadastre** | AIS vessel movement data | Free bulk | Full US waterway AIS data | Joint BOEM/NOAA/USCG project |

### Tier 4: Market Indicators

| Source | Data | Access | Granularity | Notes |
|--------|------|--------|-------------|-------|
| **Baltic Exchange** | Dry bulk freight rates | Free (delayed) | Daily BDI, Capesize, Panamax, Supramax | Via TradingEconomics, Barchart |
| **Containerized Freight Index** | Container shipping costs | Free (delayed) | Weekly Shanghai route rates | TradingEconomics |
| **UNCTAD Seaborne Trade** | Maritime trade volumes | Free reports | Annual commodity breakdowns | Review of Maritime Transport |

---

## Panjiva Data Source Analysis (Side Project)

### What Panjiva Actually Sources From

**Panjiva's core data is from publicly available government filings.** Their value-add is entity resolution, HS code imputation, value estimation, and company profiling — not exclusive data access.

#### US Data (Largest Source)

- **CBP ACE (Automated Commercial Environment)**: Inward/outward cargo manifests (bills of lading) filed with US Customs
- **FOIA disclosure**: Under 19 USC §1431, manifest data may be made available for publication
- Panjiva processes ~60,000 bills of lading per day from CBP filings
- Data available since 2007 (imports), 2009 (exports)
- Export data has ~23-day lag for regulatory reasons
- US data is maritime only (no air freight in Panjiva's US dataset)

#### Non-US Data Sources (22 countries)

| Country | Source | Access Method |
|---------|--------|---------------|
| **China** | Customs declarations via **Export to China (ETCN)** | Panjiva has exclusive searchable rights (2013 partnership) |
| **India** | Customs data (shipping bills/bills of entry) | Government customs declarations |
| **Brazil** | SECEX (Secretaria de Comércio Exterior) | Brazilian export declarations |
| **Vietnam** | Vietnam Customs | Customs declarations |
| **Indonesia** | Indonesian Customs (Bea Cukai) | Import/export declarations |
| **Chile, Colombia, Costa Rica, Ecuador, Panama, Paraguay, Peru, Uruguay, Venezuela** | Various national customs authorities | Customs declarations |
| **Pakistan, Philippines, Sri Lanka, Turkey** | National customs/bureau of statistics | Customs declarations |

#### Panjiva's Value-Add (Why They Charge $$)

1. **Entity resolution** — NLP/ML to deduplicate company names (e.g., "MSC" = "MEDITERRANEAN SHIPPING COMPANY SA")
2. **HS code assignment** — Text processing to impute HS codes from product descriptions (BoL forms don't include HS codes)
3. **Value estimation** — BoL doesn't require dollar values; Panjiva imputes from average unit values
4. **TEU calculation** — Imputed from container info
5. **Company profiling** — Cross-reference with D&B, ZoomInfo, Kompass
6. **Contact information** — Executive contacts at 1M+ companies

#### How to Access the Same Raw Data

| Source | How to Get It |
|--------|---------------|
| US CBP BoL | FOIA request via `FOIA.gov` or CBP SecureRelease portal |
| US ACE manifests | FOIA request (Data Liberation Project has template) |
| China ETCN data | Direct partnership with ETCN (exclusive to Panjiva for searchable format) |
| Country customs data | Per-country customs portals (varies by country) |

---

## HS Code Depth Reference

| HS Digits | What It Covers | Where to Get |
|-----------|----------------|--------------|
| HS2 | Chapter (e.g., "Electrical machinery") | UN Comtrade (free) |
| HS4 | Heading (e.g., "Photovoltaic cells") | UN Comtrade (free) |
| HS6 | Subheading (international standard) | UN Comtrade, ImportYeti, OEC |
| HTS8 | US tariff line | US ITC DataWeb (free) |
| HTS10 | US statistical suffix (most granular) | US ITC DataWeb (free) |
| Country-specific 8-10 digit | National tariff codes | Panjiva, national customs portals |

---

## Proposed Pipeline Architecture

```
RAW INGESTION LAYER
├── ImportYeti API ────────→ US BoL (shipment-level)
├── OEC BoL ───────────────→ US BoL (bulk historical)
├── UN Comtrade API ───────→ Global bilateral HS6
├── WTO TTD ───────────────→ Tariff actions
├── Global Fishing Watch ──→ AIS vessel presence
├── Marine Cadastre ───────→ US AIS bulk data
├── Baltic Exchange ───────→ Freight rate indices
└── US ITC DataWeb ────────→ HTS10 reference data

NORMALIZATION LAYER
├── HS code standardization (HS6 crosswalk)
├── Company name entity resolution
├── Port code normalization (UN/LOCODE)
├── Currency conversion (CIF/FOB/USD)
└── Unit normalization (TEU, kg, CBM)

STORAGE LAYER
├── Parquet files (partitioned by country/year)
├── DuckDB query layer
├── Company/entity dimension table
├── HS code reference table (HTS10)
└── Port/location dimension table
```

---

## Known Gaps in Free Data vs Panjiva

1. **Non-US customs declarations** (India, China, Brazil, Vietnam) — available through national customs portals but require per-country scraping
2. **Entity resolution** — would need to build company name deduplication
3. **HS code imputation** — BoL descriptions don't include HS codes; need a text classifier
4. **Value estimation** — CIF/FOB values not on BoL forms
5. **Air freight data** — most free sources are maritime only

---

## Next Steps

- [ ] Prototype ImportYeti API ingest
- [ ] Prototype UN Comtrade API ingest
- [ ] Prototype Global Fishing Watch AIS ingest
- [ ] Set up storage layer (Parquet + DuckDB)
- [ ] Build HS code crosswalk/reference table
- [ ] Evaluate CBP FOIA request for raw ACE manifests
