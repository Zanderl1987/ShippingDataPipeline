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
| **Dedup on partitioned inserts** | **No dedup — a weekly JODI run appends another 1.65M duplicate rows. Fix before enabling the daily schedule.** | ❗ **Not started** |
| Fix migration runner swallowing failures | `apply_pending_migrations` logs failed statements at `debug` and still marks the migration applied | Not started |
| Fix `dashboard.py` `_build_html` | Returns a tuple → `write_text` TypeError, 4 test failures | ⏳ In progress (separate session) |

### Priority tables for the data-lake join

| Table | Rows | Source |
|-------|------|--------|
| `chokepoint_transits` | 77,389 (2019-01-01 → 2026-07-26) | IMF PortWatch |
| `chokepoint_status` | 6 | Eagle Intelligence |
| `freight_rates` | **0** | ❌ no working free source — see Deferred |

## API Key Setup

| Task | Details | Status |
|------|---------|--------|
| Register for EIA API key | Free at eia.gov/open, needed for `eia_petroleum.py` | ✅ Done |
| Register for AISStream API key | Free via GitHub OAuth, needed for `aisstream.py` WebSocket | ✅ Done |
| Hormuz Monitor | Listed as having free tier but registration yields only paid plans. NO-GO. | ❌ No free tier |
| Set up `.env` with API keys | Added `EIA_API_KEY`, `AISSTREAM_API_KEY` | ✅ Done |
| Set up GitHub secrets | `EIA_API_KEY`, `AISSTREAM_API_KEY` set as repo secrets | ✅ Done |
| Test GitHub Actions workflow | Ran twice — first failed on lint, second triggered after fix | ⏳ In progress |

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
| DATA_SOURCES.md update | Add new collectors (Barcelona, DMA, Equasis, FBX, Singapore MPA) — document these as **NO-GO**, not as working sources; their endpoints 404 (see Deferred) | Not started |

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

> **"Collector built" is not evidence a source works.** All five rows above were
> written against endpoints that had never been called successfully. Each returns
> 0 rows, and because `collect_data()` swallows its own exceptions and returns 0,
> the orchestrator reports them as `[OK]`. Verified 2026-07-30.

---

*Last updated: 2026-07-30 (Session 13)*
