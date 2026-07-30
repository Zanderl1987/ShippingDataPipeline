# Remaining Work

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
| DATA_SOURCES.md update | Add new collectors (Barcelona, DMA, Equasis, FBX, Singapore MPA) | Not started |

## Deferred / Low Priority

| Task | Details | Status |
|------|---------|--------|
| OpenAIS integration | Self-hosted only, deferred | Blocked |
| Baltic Exchange trial | 1-week free trial for freight rate data | Not started |
| Danish Maritime Authority (DMA) | European waters daily AIS files | ✅ Collector built (`dma_collector.py`) |
| Singapore OCEANS-X | Transshipment hub data | ✅ Collector built (`singapore_oceanx_collector.py`) |
| Barcelona Port Authority | Port call data | ✅ Collector built (`barcelona_port_collector.py`) |
| Equasis | Ship inspection data | ✅ Collector built (`equasis_collector.py`) |
| FBX (Freightos Baltic Index) | Container freight rates | ✅ Collector built (`fbx_collector.py`) |

---

*Last updated: 2026-07-30 (Session 12)*
