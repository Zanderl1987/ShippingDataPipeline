# Adversarial Code Review — Shipping Data Pipeline

**Reviewer:** big-pickle (automated adversarial review)
**Date:** 2026-07-27
**Scope:** All source files in `src/`, `tests/`, project config, staging docs
**Method:** Read every file line-by-line; classifies bugs by concrete production impact

---

## CRITICAL — Will cause data corruption or security breach in production

### C1. SQL Injection in `curation/dedup.py:deduplicate_table()`
**File:** `src/curation/dedup.py:150-159`
**Impact:** `table_name` and `key_columns` are interpolated directly into SQL via f-strings. Any caller passing user-controlled input (or any string at all) can execute arbitrary SQL. The generic function is a standing invitation for injection.
```python
conn.execute(f"""
    DELETE FROM {table_name}
    WHERE rowid NOT IN (
        SELECT MIN(rowid)
        FROM {table_name}
        GROUP BY {key_cols}
    )
""")
```
**Fix:** Validate `table_name` against `ALL_TABLES` (same allowlist as `reader.py`). Validate `key_columns` against `information_schema.columns` for the target table. Never f-string table or column names into SQL.

---

### C2. SQL Injection in `curation/validation.py` — All validation functions
**File:** `src/curation/validation.py:71-72, 105-106, 124`
**Impact:** `validate_not_null()`, `validate_range()`, and `validate_positive()` all interpolate `table` and `column` directly into SQL via f-strings. An attacker-controlled table or column name allows arbitrary SQL execution.
```python
total = _get_count(conn, f"SELECT count(*) FROM {table}")
null_count = _get_count(conn, f"SELECT count(*) FROM {table} WHERE {column} IS NULL")
```
**Fix:** Validate `table` against `ALL_TABLES`. Validate `column` against `information_schema.columns` for the given table. Use a helper function like `_validate_column_name(table, column, conn)`.

---

### C3. SQL Injection in `imf_portwatch.py:get_daily_chokepoint_data()`
**File:** `src/collectors/imf_portwatch.py:80-91`
**Impact:** `start_date` and `end_date` are interpolated directly into the ArcGIS WHERE clause via f-strings. A malicious date string like `"x'; DROP TABLE foo; --"` would execute arbitrary SQL against the ArcGIS service.
```python
where_clause += f" AND date >= TIMESTAMP '{start_date} 00:00:00'"
# ...
params["where"] += f" AND date <= TIMESTAMP '{end_date} 23:59:59'"
```
**Fix:** Validate that `start_date` and `end_date` match `YYYY-MM-DD` format with a regex before interpolation. Better yet, use ArcGIS temporal query parameters.

---

### C4. `write_raw()` returns total count, not rows-written-this-run
**File:** `src/storage/writer.py:65-72`
**Impact:** After inserting rows, the function queries `SELECT count(*) FROM {table} WHERE source = ?`, which returns the **cumulative** total for that source, not the delta from this write. Every caller gets a wrong `rows_written` count. This corrupts:
- `source_tracking` table (misleading `rows_written`)
- All notifications (reports "wrote 50,000 rows" when only 200 were new)
- Collection reports and dashboards
```python
row = conn.execute(
    f"SELECT count(*) FROM {table_name} WHERE source = ?",
    [source],
).fetchone()
assert row is not None
count: int = row[0]  # BUG: this is the TOTAL, not the delta
conn.close()
return count
```
**Fix:** Capture `df.height` before insert and return that as the count:
```python
rows_written = df.height
# ... perform INSERT ...
return rows_written
```

---

### C5. DuckDB connections leak on exceptions
**File:** `src/storage/writer.py:31, 82` (write_raw, write_curated)
**Impact:** `duckdb.connect()` opens a connection, but no `try/finally` wraps the logic. Any exception between `conn = duckdb.connect(...)` and `conn.close()` leaks the connection. Over time this exhausts file handles and locks the database file. In production with multiple concurrent collectors, this causes `PermissionError` or "database is locked" errors.
```python
conn = duckdb.connect(str(db_path))  # opened
# ... if exception here, conn never closed ...
conn.close()  # never reached
```
**Fix:** Wrap all connection usage in `try/finally`:
```python
conn = duckdb.connect(str(db_path))
try:
    # ... work ...
    return count
finally:
    conn.close()
```

---

### C6. `vessels` table PRIMARY KEY fails on NULL IMO
**File:** `src/storage/schema.py:48`
**Impact:** The `vessels` table has `imo BIGINT PRIMARY KEY`. Multiple collectors (axiomancer port positions at `axiomancer.py:121`, tankermap at `tankermap.py:79`, seafarer_index at `seafarer_index.py:74`) can produce vessel records with NULL IMO. DuckDB only allows one NULL in a PRIMARY KEY column. The second NULL-IMO insert will raise a constraint violation, silently failing the entire batch.
```python
# axiomancer.py:121 — imo can be None
imo = int(imo_str) if imo_str else None
```
**Fix:** Either (a) remove the PRIMARY KEY constraint and use a composite unique constraint `(imo, source)` with NULL handling, or (b) filter out records with NULL IMO before writing to `vessels`, or (c) use a synthetic key.

---

### C7. SQL injection in `write_curated()`
**File:** `src/storage/writer.py:86-89`
**Impact:** `dataset` parameter is interpolated into SQL. If a caller passes a malicious dataset name, arbitrary SQL executes.
```python
conn.execute(
    f"CREATE TABLE IF NOT EXISTS curated_{dest} AS SELECT * FROM df WHERE 1=0;"
)
conn.execute(f"INSERT OR REPLACE INTO curated_{dest} SELECT * FROM df;")
```
**Fix:** Validate `dest` against an allowlist of known dataset names, or sanitize to `[a-zA-Z0-9_]` only.

---

## MEDIUM — Silent failures, data quality issues, or incorrect behavior

### M1. `collect_all.py:run_collector()` counts stale skips as success
**File:** `src/monitoring/collect_all.py:277-281`
**Impact:** When a collector is skipped due to recent collection, it returns `success=True` with `error="Skipped (recently collected)"`. The `CollectionReport.succeeded` count inflates, masking that data was not refreshed. The collection report shows "0 failed" even when many sources are stale.
```python
return CollectionResult(
    source=collector.name,
    success=True,  # BUG: should not count skip as success
    error="Skipped (recently collected)",
)
```
**Fix:** Add a separate `skipped` field to `CollectionResult` and track skipped sources separately from successes.

---

### M2. `aisstream.py` `websockets` not in project dependencies
**File:** `src/collectors/aisstream.py:100`
**Impact:** `import websockets` will raise `ImportError` for anyone who hasn't manually installed it. It's not listed in `pyproject.toml` dependencies. The AISStream collector is a registered collector in `get_collectors()`, so it will crash every collection run.
**Fix:** Add `websockets>=12.0` to `pyproject.toml` dependencies, or move it to `[project.optional-dependencies]`.

---

### M3. `dashboard.py` XSS via unescaped HTML injection
**File:** `src/monitoring/dashboard.py:96-99, 107-112`
**Impact:** Table names, source names, and stale-source details are injected directly into HTML without escaping. If a source name contains `<script>alert('x')</cript>`, it would execute in any browser viewing the dashboard.
```python
f"<tr><td>{t.table_name}</td>"  # no HTML escaping
f"<td>{s.source}</td>"
```
**Fix:** Use `html.escape()` on all dynamic values before inserting into HTML.

---

### M4. `config.py:from_env()` crashes on non-numeric SMTP_PORT
**File:** `src/config.py:51`
**Impact:** If `SMTP_PORT` is set to a non-numeric value (e.g. `"587/tcp"`), `int(os.getenv("SMTP_PORT", "587"))` raises `ValueError`, crashing the entire application at import time.
```python
smtp_port=int(os.getenv("SMTP_PORT", "587")) if os.getenv("SMTP_PORT") else None,
```
**Fix:** Wrap in try/except:
```python
def _safe_int(val: str | None, default: int) -> int:
    if val is None:
        return default
    try:
        return int(val)
    except ValueError:
        return default
```

---

### M5. `reader.py:list_sources()` reads every parquet file to count rows
**File:** `src/storage/reader.py:87-93`
**Impact:** For large datasets (NOAA bulk downloads can be GBs), `list_sources()` reads every parquet file to sum row counts. This is O(total_rows) and takes minutes for production data. Called by `cmd_overview` and dashboard generation.
```python
total_rows = sum(
    pl.read_parquet(str(f)).height for f in parquet_files
)
```
**Fix:** Use parquet metadata (footer) to get row counts without reading full files. Polars supports `pl.read_parquet(path, n_rows=0).height` for metadata-only row counts, or use `pyarrow.parquet.ParquetFile(path).metadata.num_rows`.

---

### M6. Dedup `deduplicate_vessels()` keeps wrong duplicate
**File:** `src/curation/dedup.py:73-80`
**Impact:** Uses `MAX(rowid)` which keeps the **last inserted** duplicate, not the most recent by business timestamp. If an older vessel record is inserted after a newer one (e.g., re-collection), the stale data wins.
```python
DELETE FROM vessels
WHERE rowid NOT IN (
    SELECT MAX(rowid)  # keeps LAST inserted, not most recent
    FROM vessels
    GROUP BY imo
)
```
**Fix:** Use `ingested_at` to determine which duplicate to keep:
```python
SELECT MAX(ingested_at) ... GROUP BY imo
-- then delete all except the one with the latest ingested_at
```

---

### M7. `enrichment.py:create_curated_ais_positions()` drops and recreates table
**File:** `src/curation/enrichment.py:122-123`
**Impact:** `DROP TABLE IF EXISTS curated_ais_positions` destroys the curated table before recreating. Any concurrent reader will see an empty table or get an error. This also means the enrichment step is not incremental — it rebuilds the entire curated table every time.
**Fix:** Use `CREATE OR REPLACE TABLE` or materialize into a temp table then atomically rename.

---

### M8. `noaa_marinecadastre.py:download_file()` has no integrity check
**File:** `src/collectors/noaa_marinecadastre.py:62-69`
**Impact:** Downloaded parquet files are ingested without checksum verification. A partial download (network interruption) or corrupted file will produce garbage data in the database with no indication of corruption.
**Fix:** Add SHA256 checksum verification after download, or at minimum validate file size is > minimum expected size.

---

### M9. `seafarer_index.py:collect_ports()` loads 43.5MB JSON into memory
**File:** `src/collectors/seafarer_index.py:40-45`
**Impact:** The full port registry is a 43.5MB JSON response loaded entirely into memory as a Python list, then converted to a Polars DataFrame. On constrained environments (GitHub Actions free tier), this can cause OOM.
**Fix:** Stream the JSON response with `ijson` or process in chunks. Or accept the memory cost and document the requirement.

---

### M10. No retry logic for any API calls
**Files:** All collectors (every `requests.get/post` call)
**Impact:** Transient network failures (DNS timeout, connection reset, HTTP 502/503/429) cause immediate collection failure. In production on GitHub Actions, this means a single hiccup skips an entire day's collection.
**Fix:** Add `tenacity` or `urllib3.util.retry.Retry` with exponential backoff for transient errors (429, 502, 503, 504, connection errors).

---

### M11. `monitoring/quality.py:get_table_quality()` silently swallows exceptions
**File:** `src/monitoring/quality.py:111, 149`
**Impact:** `except Exception: pass` hides all quality check errors. If a table has an unexpected schema (missing columns, type mismatches), quality metrics are silently omitted from the report. The dashboard shows "no issues" when there are undetected issues.
```python
except Exception:
    pass  # silently swallows all errors
```
**Fix:** Log the exception at WARNING level and propagate critical errors:
```python
except Exception as e:
    logger.warning("Quality check failed for %s.%s: %s", table_name, col_name, e)
```

---

### M12. `collect_all.py:get_collectors()` schedule thresholds may be inverted
**File:** `src/monitoring/collect_all.py:270-271`
**Impact:** "daily" sources have a 1-hour staleness threshold, "weekly" sources have a 24-hour threshold. This means daily sources re-fetch every hour (wasting API quota on limited sources like VesselAPI at 150 calls/month), while weekly sources allow 24-hour gaps. The naming is confusing and the thresholds seem backwards.
```python
threshold = 1.0 if collector.schedule == "daily" else 24.0
```
**Fix:** Rename to match actual behavior, or adjust: daily=24h, weekly=168h (7 days). Also add `ondemand` handling.

---

### M13. `aisstream.py` WebSocket disconnect silently drops messages
**File:** `src/collectors/aisstream.py:143-148`
**Impact:** If `asyncio.wait_for` times out, any messages received during the `_receive()` coroutine are only counted by the in-memory list. But if the WebSocket closes unexpectedly before timeout, the `async for` loop ends silently and no error is reported.
**Fix:** Log a warning when the WebSocket disconnects unexpectedly (before timeout). Consider implementing reconnection logic.

---

### M14. `writer.py:write_raw()` silently drops columns not in target table
**File:** `src/storage/writer.py:44`
**Impact:** Columns in the DataFrame that don't exist in the target table are silently dropped. If a collector adds a new field, it will be silently lost with no warning.
```python
insert_cols = [c for c in df.columns if c != "ingested_at" and c in table_columns]
```
**Fix:** Log a warning when columns are dropped:
```python
dropped = set(df.columns) - set(insert_cols) - {"ingested_at"}
if dropped:
    logger.warning("Dropping columns not in %s: %s", table_name, dropped)
```

---

## LOW — Code smells, minor inefficiencies, or test gaps

### L1. `config.py:settings` module-level singleton
**File:** `src/config.py:65`
**Impact:** `settings = Settings.from_env()` is evaluated at import time. Tests that monkeypatch environment variables after import may not affect already-imported settings. The test `test_config.py` works because it calls `Settings.from_env()` directly, but integration tests using the module-level `settings` singleton may behave differently.
**Fix:** Use a lazy-loading pattern or a `get_settings()` function that re-reads from environment.

---

### L2. `reports.py:cmd_overview()` uses f-string for table names
**File:** `src/analytics/reports.py:51`
**Impact:** While the table names come from a hardcoded list (safe), the pattern is inconsistent with the SQL injection fix applied to `reader.py`. It sets a bad example for future contributors.
```python
result = query(f"SELECT count(*) as row_count FROM {table}")
```
**Fix:** Use parameterized queries or a helper function that validates table names.

---

### L3. `reports.py:cmd_weather()` references `sea_surface_temperature` from `marine_weather`
**File:** `src/analytics/reports.py:137`
**Impact:** The SQL references `marine_weather.sea_surface_temperature` and `wind_speed_10m` from `weather` table, but the query only reads from `marine_weather`. If `wind_speed_10m` isn't in `marine_weather` schema, the query will fail.
**Fix:** Verify the column exists in `marine_weather` or join with `weather` table.

---

### L4. `tracker.py:SourceTracker` connection never closed in most callers
**File:** `src/storage/tracker.py:24-35`
**Impact:** `SourceTracker._get_conn()` opens a DuckDB connection that's never closed by most callers. The `close()` method exists but is never called in the main collection flow (collectors create a tracker, use it, and let it go out of scope).
**Fix:** Make `SourceTracker` a context manager (`__enter__`/`__exit__`) or call `close()` in `TimedCollector.__exit__`.

---

### L5. `eia_petroleum.py:_parse_eia_stocks_response()` loses product facet data
**File:** `src/collectors/eia_petroleum.py:125-127`
**Impact:** The facet extraction logic only takes the first facet element. EIA responses may have multiple facets per record; only the first is captured. The `product_name` may be a facet ID rather than a human-readable name.
```python
product_name = facets[0] if isinstance(facets, list) and facets else ""
```
**Fix:** Examine the EIA API response structure more carefully and map facet IDs to names.

---

### L6. `curation/pipeline.py:run_curation()` doesn't pass tracker
**File:** `src/curation/pipeline.py:89-136`
**Impact:** The curation pipeline creates its own DuckDB connection but doesn't use `SourceTracker` to record curation runs. There's no audit trail of when curation was last run.
**Fix:** Accept an optional `SourceTracker` and record curation events.

---

### L7. `collect_all.py:get_collectors()` uses `try/except ImportError` for all imports
**File:** `src/monitoring/collect_all.py:76-229`
**Impact:** Every collector import is wrapped in `try/except ImportError`. This means if a collector has a runtime import error (e.g. missing dependency), it's silently swallowed and the collector is simply not registered. No warning is shown to the user about what went wrong.
**Fix:** Log the actual import error at DEBUG level, or at least include the error message in the warning.

---

### L8. No test for `write_curated()`
**File:** `src/storage/writer.py:75-94`
**Impact:** `write_curated()` has no dedicated test coverage. It's used in `enrichment.py` but the enrichment tests may not exercise all code paths.
**Fix:** Add unit tests for `write_curated()`.

---

### L9. `open_meteo.py` collects from a single hardcoded point
**File:** `src/monitoring/collect_all.py:105`
**Impact:** The Open-Meteo collector in `get_collectors()` is hardcoded to latitude=1.264, longitude=103.82 (Singapore). This means only Singapore weather is collected. Major shipping lanes (English Channel, Strait of Malacca, Suez approach) have no weather coverage.
**Fix:** Make the coordinates configurable, or collect from multiple key locations.

---

### L10. `un_comtrade.py:collect_trade_data()` hardcodes China
**File:** `src/monitoring/collect_all.py:196`
**Impact:** The UN Comtrade collector is hardcoded to `reporter_code=156` (China). Global trade analysis requires data from all major economies. Only collecting Chinese trade data creates a severe blind spot.
**Fix:** Make reporter codes configurable, or implement a loop over top 20 trade nations.

---

### L11. `tankermap.py:collect_vessels()` writes to `ais_positions` without `partition_date`
**File:** `src/collectors/tankermap.py:187`
**Impact:** TankerMap vessel data is written via `write_raw(SOURCE, df)` which defaults to `table_name="ais_positions"`. But tanker-specific fields (cargo estimates) from the API response are lost because `write_raw` only keeps columns present in the target table.
**Fix:** Either add tanker-specific columns to `ais_positions` schema, or write to a dedicated `tanker_positions` table.

---

### L12. `barentswatch.py:get_vessel_track()` URL construction is fragile
**File:** `src/collectors/barentswatch.py:78`
**Impact:** URL path is constructed via string interpolation without validation. If `mmsi` or `hours` values are unexpected (e.g. negative), the URL will be malformed.
```python
url = f"{HISTORIC_URL}/historic/trackslast{hours}hours/{mmsi}"
```
**Fix:** Validate `mmsi > 0` and `0 < hours <= 336` before constructing URL.

---

### L13. `test_storage.py:test_init_db()` checks `sqlite_master` instead of DuckDB
**File:** `tests/test_storage.py:33`
**Impact:** The test queries `sqlite_master` which is SQLite-specific. DuckDB uses `information_schema.tables` or `duckdb_tables()`. This test may pass only because DuckDB provides a compatibility shim, but it's fragile and non-portable.
```python
tables = conn.execute(
    "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
).fetchall()
```
**Fix:** Use DuckDB-native introspection:
```python
tables = conn.execute(
    "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
).fetchall()
```

---

### L14. `notify.py:Notifier._send_webhook()` uses `urllib.request` instead of `requests`
**File:** `src/monitoring/notify.py:147-157`
**Impact:** Uses `urllib.request.urlopen` while the rest of the codebase uses `requests`. This inconsistency means retry logic, timeout handling, and SSL verification behavior differs from the rest of the pipeline.
**Fix:** Refactor to use `requests.post()` for consistency.

---

## Summary by Severity

| Severity | Count | Key Themes |
|----------|-------|------------|
| CRITICAL | 7 | SQL injection (3), data corruption (2), connection leaks (1), PK constraint (1) |
| MEDIUM | 14 | Silent failures (4), XSS (1), missing deps (1), no retry (1), OOM risk (1) |
| LOW | 14 | Code smells (5), test gaps (3), hardcoded values (2), minor issues (4) |
| **Total** | **35** | |

---

## EXPANSION PLAN

### 1. New Data Sources — Highest Impact

| Source | Why | Effort |
|--------|-----|--------|
| **Danish Maritime Authority (DMA)** | European waters daily AIS files. Closes the biggest geographic gap (only US + Norwegian + Singapore waters covered now). | Medium — bulk file download, similar to NOAA pattern |
| **Freightos Baltic Index (FBX)** | Container freight rates. The most-requested metric for trade analysis. The `freight_rates` table exists but has no collector. | Medium — web scraping or API trial |
| **Port of Barcelona OpenInfoAPI** | Open, no-auth port API. Template for other port-specific data. | Low — REST JSON, straightforward |
| **Singapore OCEANS-X** | World's largest transshipment hub. Currently only Open-Meteo weather covers Singapore. | Medium — requires registration |
| **Equasis** | Ship safety/quality/inspection data. Unique enrichment for vessel risk scoring. | Medium — requires registration + CSV export |

### 2. Analytics to Add

| Analysis | Data Needed | Value |
|----------|-------------|-------|
| **Oil Flow Tracker** | Correlate TankerMap positions + JODI trade data + EIA imports | See oil moving from producer to consumer in near-real-time |
| **Chokepoint Risk Dashboard** | Eagle Intelligence + PortWatch + Hormuz Monitor + AIS positions near chokepoints | Single-view geopolitical risk assessment |
| **Vessel Anomaly Detection** | AIS positions + port calls + loitering events (GFW) | Detect dark fleet activity, sanctions evasion, illegal fishing |
| **Freight Rate Prediction** | FBX historical + port congestion + oil prices + weather | Predict container rate movements 2-4 weeks ahead |
| **Supply Chain Delay Estimator** | AIS dwell times + weather + chokepoint status + port congestion | Quantify expected delays for specific routes |
| **Emissions Estimator** | AIS tracks + vessel specs + fuel type | IMO DCS-compliant CO2 emissions per voyage |
| **Port Efficiency Scorecard** | Port calls + dwell times + congestion + weather | Rank port performance over time |

### 3. Production-Readiness Gaps

| Gap | Current State | What's Needed |
|-----|---------------|---------------|
| **Retry/backoff** | No retries on any API call | `tenacity` with exponential backoff for 429/502/503/504 |
| **Rate limit tracking** | None | Track per-source API usage, auto-throttle when approaching limits |
| **Database locking** | No concurrency control | File-based lock or DuckDB's built-in WAL mode for concurrent reads |
| **Backfill support** | Manual, per-source | Unified backfill CLI command with date range and source selection |
| **Data lineage** | Minimal (only `source` column) | Full lineage: collection timestamp → curation run → analytics version |
| **Monitoring alerts** | Webhook/email only | PagerDuty/OpsGenie integration, structured alert rules |
| **Incremental curation** | Full rebuild every time | Delta-based enrichment that only processes new/changed records |
| **Schema migration** | `CREATE TABLE IF NOT EXISTS` only | Alembic-style schema versioning for table evolution |

### 4. Cross-Referencing with Financial Data Pipeline

| Cross-Reference | Shipping Data | Financial Data | Insight |
|-----------------|---------------|----------------|---------|
| **Oil prices ↔ Tanker positions** | TankerMap VLCC positions, Hormuz Monitor Brent/WTI | Brent futures, crude ETF flows | Predict oil price moves from physical supply |
| **Chokepoint risk ↔ Market volatility** | Eagle Intelligence risk scores | VIX, commodity volatility indices | Quantify geopolitical risk premium |
| **Port congestion ↔ Supply chain stocks** | AIS dwell times at major ports | Supply chain ETFs, retailer inventory data | Early warning for supply chain disruptions |
| **Trade flows ↔ FX rates** | UN Comtrade bilateral trade | USD/CNY, USD/EUR, trade-weighted dollar | Predict currency moves from trade balance shifts |
| **Vessel tracking ↔ Insurer exposure** | AIS positions in risk zones | Marine insurance rates, P&I club data | Real-time exposure calculation |
| **Freight rates ↔ Inflation** | FBX container rates | CPI, PPI, shipping cost components | Leading indicator for import price inflation |

### 5. Monitoring/Alerting Gaps

| Gap | Impact | Fix |
|-----|--------|-----|
| **No alert on data volume anomalies** | A source returning 90% fewer rows (API change) isn't flagged | Add Z-score or percentile-based anomaly detection on row counts |
| **No cross-source correlation alerts** | TankerMap shows 0 tankers but Hormuz Monitor shows normal traffic — no contradiction detected | Add cross-source consistency checks |
| **No alert on schema drift** | API changes silently drop new fields | Schema diff check on each collection run |
| **No collection duration tracking** | Slow APIs that timeout aren't detected | Alert on duration > 2σ from rolling average |
| **No data freshness SLA** | Each source has different freshness requirements | Per-source configurable SLA with escalation tiers |
| **No notification dedup** | Same stale-source alert fires every collection run | Deduplicate notifications within configurable window |
| **No dashboard auto-refresh** | Dashboard is static HTML, must regenerate manually | Add webhook-triggered regeneration or scheduled rebuild |

---

*Review complete. 35 findings across 3 severity levels. The 7 CRITICAL issues should be addressed before any production deployment. The SQL injection vectors (C1, C2, C3) and the write_raw count bug (C4) are the highest-priority fixes.*
