# Remaining Work

## Immediate — Oil Source Live Testing

| Task | Details | Status |
|------|---------|--------|
| Register for EIA API key | Free at eia.gov/open, needed for `eia_petroleum.py` | Not started |
| Register for Hormuz Monitor API key | Free tier 60 req/hr, needed for `hormuz_monitor.py` | Not started |
| Register for AISStream API key | Free via GitHub OAuth, needed for `aisstream.py` WebSocket | Not started |
| Set up `.env` with API keys | Add `EIA_API_KEY`, `HORMUZ_API_KEY`, `AISSTREAM_API_KEY` | Blocked on registrations |
| Set up GitHub secrets | Add API keys to repository secrets for Actions workflow | Not started |
| Test GitHub Actions workflow | Verify workflow runs correctly on schedule | Pending |

## Phase 4 — Automation & Polish ✓ COMPLETE

| Task | Details | Status |
|------|---------|--------|
| GitHub Actions workflow | `.github/workflows/collect.yml` — daily at 06:00 UTC | ✅ Done |
| Notification on failures | Slack/Discord webhook, email, log file via `src/monitoring/notify.py` | ✅ Done |
| Data quality monitoring | `src/monitoring/quality.py` — row counts, null rates, staleness | ✅ Done |
| CLI reporting (`sdp` command) | Added `status`, `collect`, `quality` commands | ✅ Done |
| Collection orchestrator | `src/monitoring/collect_all.py` — runs all collectors with staleness checks | ✅ Done |
| Optional dashboard | Streamlit or static HTML for visual overview | Not started |

## Documentation

| Task | Details | Status |
|------|---------|--------|
| README usage guide | Install, configure, run collectors, query data | Not started |
| Module-level docstrings | All collectors have basic docstrings, need review | In progress |
| DATA_SOURCES.md update | Add new oil sources (EIA, JODI, PortWatch, TankerMap, Hormuz) | Not started |

## Deferred / Low Priority

| Task | Details | Status |
|------|---------|--------|
| OpenAIS integration | Self-hosted only, deferred | Blocked |
| Baltic Exchange trial | 1-week free trial for freight rate data | Not started |
| Danish Maritime Authority | European waters daily AIS files | Not started |
| Singapore OCEANS-X | Transshipment hub data | Not started |

---

*Last updated: 2026-07-23 (Session 10)*
