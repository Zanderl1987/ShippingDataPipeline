#!/usr/bin/env python3
"""
Upload the shipping data pipeline's DuckDB tables to HuggingFace as a dataset.

Unlike financial-data-pipeline (which has a dedicated storage/curated/**/*.parquet
tree), this pipeline's canonical, deduped data lives directly in DuckDB
(storage/pipeline.db) -- storage/parquet/raw/<source>/ holds per-partition raw
exports only, not the deduped table. So this script exports each non-empty,
non-internal DuckDB table to a single parquet file before uploading.

Usage:
    python upload_huggingface.py [--repo-name shipping-data-pipeline] [--private]

Requires HUGGINGFACE_TOKEN or HF_TOKEN env variable (in .env).
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from dotenv import load_dotenv
from huggingface_hub import HfApi, login

load_dotenv(Path(__file__).parent / ".env")

from src.config import settings  # noqa: E402  (needs env vars from load_dotenv)
from src.storage.schema import ALL_TABLES  # noqa: E402

TABLE_DESCRIPTIONS = {t.name: t.description for t in ALL_TABLES} | {
    # Rebuilt by curation every run, so not in the schema.
    "chokepoint_daily": (
        "Derived: daily traffic per PortWatch chokepoint, 7-day averages vs a "
        "year earlier and the prior 28 days, plus GDACS hazards within 500 km"
    ),
    "port_congestion_proxy": "Derived: each port's latest 7-day vs 90-day average port calls",
    "curated_ais_positions": "Derived: ais_positions joined with vessel and port details",
    "curated_vessels": "Derived: vessels with vessel age added",
}

EXPORT_DIR = Path(__file__).parent / "storage" / "parquet" / "hf_export"

# Pipeline-internal bookkeeping tables -- not data, don't publish.
INTERNAL_TABLES = {"schema_migrations", "schema_versions", "source_tracking", "lineage_events"}

README_TEMPLATE = """---
language:
  - en
tags:
  - shipping
  - maritime
  - logistics
  - trade
  - alternative-data
  - pipeline
task_categories:
  - other
size_categories:
  - {size_category}
---

# Shipping Data Pipeline — Full Curated Snapshot

Maritime trade, vessel tracking, and chokepoint transit data covering **{n_tables} tables**
and **{n_rows:,} rows**.

## Data Sources

| Table | Rows | Description |
|---|---|---|
{table_rows}

## Usage

Each table lives in its own subfolder (`<table>/<table>.parquet`) -- load one
directly with `pandas` or `polars`, or point `datasets` at a single table's
parquet file:

```python
import pandas as pd

df = pd.read_parquet("hf://datasets/{repo_id}/{first_table}/{first_table}.parquet")
```

```python
from datasets import load_dataset

ds = load_dataset(
    "parquet",
    data_files=f"hf://datasets/{repo_id}/{first_table}/{first_table}.parquet",
)
```

## Engineering & data quality

- {tests_line}, run through a CI pipeline (lint → type check → test → collect → quality
  gate) that also runs the daily collection itself.
- **Deduplication and lineage tracking**: raw per-partition exports land in DuckDB, then a
  dedup/curation layer resolves the canonical table published here; source-tracking and
  lineage-event tables record where each row came from (kept internal, not part of this
  public export).

## Build Info

- **Generated**: {generated_date}
- **Pipeline**: ShippingDataPipeline (https://github.com/Zanderl1987/ShippingDataPipeline)
- **Tables**: {n_tables}
- **Total Rows**: {n_rows:,}
- **Total Size**: {total_size_mb:.1f} MB

## License and attribution

CC BY 4.0 — data sourced from public APIs and government/intergovernmental databases.
Upstream terms still apply to each source's data. In particular:

- `port_activity`, `port_profiles`, `trade_nowcast`, `chokepoint_transits`,
  `disruption_events`: **Source: International Monetary Fund, PortWatch**
  (https://portwatch.imf.org), used under the IMF copyright and usage terms
  (https://www.imf.org/en/about/copyright-and-terms). IMF data is available free
  of charge from the IMF. Columns are renamed and retyped; values are unaltered.
- `disruption_events` also derives from GDACS, the Global Disaster Alert and
  Coordination System (https://www.gdacs.org).
"""


def export_tables(db_path: Path) -> list[tuple[str, int, int]]:
    """Export each non-empty, non-internal DuckDB table to its own parquet file,
    one subfolder per table (<table>/<table>.parquet) -- mirrors the
    financial-data-pipeline convention so each table is a distinct, browsable
    unit on HF instead of a flat file dump, and so HF's config auto-detection
    has a chance of picking up per-table splits.

    Returns a list of (table_name, row_count, file_size_bytes).
    """
    if EXPORT_DIR.exists():
        for stale in EXPORT_DIR.glob("*"):
            if stale.is_dir():
                for f in stale.glob("*.parquet"):
                    f.unlink()
                stale.rmdir()
            elif stale.suffix == ".parquet":
                stale.unlink()
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)

    conn = duckdb.connect(str(db_path), read_only=True)
    try:
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'main' ORDER BY table_name"
        ).fetchall()

        stats = []
        for (name,) in tables:
            if name in INTERNAL_TABLES:
                continue
            count = conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0]
            if count == 0:
                continue
            table_dir = EXPORT_DIR / name
            table_dir.mkdir(exist_ok=True)
            out_path = table_dir / f"{name}.parquet"
            conn.execute(
                f'COPY (SELECT * FROM "{name}") TO ? (FORMAT PARQUET, COMPRESSION ZSTD)',
                [str(out_path)],
            )
            stats.append((name, count, out_path.stat().st_size))
        return stats
    finally:
        conn.close()


def size_category(n_rows: int) -> str:
    """HF's size_categories tag, which buckets by row count."""
    for limit, label in [
        (1_000, "n<1K"),
        (10_000, "1K<n<10K"),
        (100_000, "10K<n<100K"),
        (1_000_000, "100K<n<1M"),
        (10_000_000, "1M<n<10M"),
        (100_000_000, "10M<n<100M"),
    ]:
        if n_rows < limit:
            return label
    return "100M<n<1B"


def count_tests() -> int | None:
    """Count the test suite via pytest --collect-only, for the dataset card.

    Returns None (rather than a stale hardcoded number) if collection fails
    for any reason -- the card falls back to not stating a count.
    """
    try:
        result = subprocess.run(
            [sys.executable, "-m", "pytest", "--collect-only", "-q"],
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=60,
        )
        match = re.search(r"(\d+) tests? collected", result.stdout)
        return int(match.group(1)) if match else None
    except Exception:
        return None


def main(repo_name: str = "shipping-data-pipeline", private: bool = False) -> None:
    token = os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN")
    if not token:
        print("ERROR: Set HUGGINGFACE_TOKEN or HF_TOKEN env variable.")
        return

    db_path = settings.storage_dir / "pipeline.db"
    if not db_path.exists():
        print(f"ERROR: No DuckDB database at {db_path}")
        return

    print(f"Exporting tables from {db_path} ...")
    stats = export_tables(db_path)

    if not stats:
        print("No non-empty tables to upload.")
        return

    total_rows = sum(s[1] for s in stats)
    total_size_mb = sum(s[2] for s in stats) / 1024 / 1024

    print(f"\n{len(stats)} tables, {total_rows:,} rows, {total_size_mb:.1f} MB")
    for name, count, size in stats:
        print(f"  {name}: {count:,} rows ({size / 1024 / 1024:.2f} MB)")

    login(token=token)
    api = HfApi()

    repo_id = f"ZanderL1337/{repo_name}"
    print(f"\nCreating/updating repo: {repo_id} (private={private})")
    api.create_repo(repo_id, repo_type="dataset", private=private, exist_ok=True)

    # create_repo's `private` only applies when it actually creates the repo --
    # with exist_ok=True it silently no-ops on an existing one, so --private
    # would print "private=True" and still publish to a public repo. Found the
    # hard way 2026-08-10: this repo had existed (public, empty) since 08-03, so
    # the first real upload went out publicly despite --private. Enforce the
    # requested visibility explicitly, BEFORE any data is uploaded.
    current = api.dataset_info(repo_id).private
    if current != private:
        print(f"  repo already existed with private={current}; setting private={private}")
        api.update_repo_settings(repo_id=repo_id, repo_type="dataset", private=private)

    table_rows = "\n".join(
        f"| {name} | {count:,} | {TABLE_DESCRIPTIONS.get(name, '')} |"
        for name, count, _ in stats
    )
    n_tests = count_tests()
    tests_line = f"**{n_tests} tests**" if n_tests is not None else "Full pytest suite"
    readme = README_TEMPLATE.format(
        repo_id=repo_id,
        n_tables=len(stats),
        tests_line=tests_line,
        n_rows=total_rows,
        total_size_mb=total_size_mb,
        generated_date=datetime.now(UTC).strftime("%Y-%m-%d"),
        first_table=stats[0][0],
        table_rows=table_rows,
        size_category=size_category(total_rows),
    )
    (EXPORT_DIR / "README.md").write_text(readme, encoding="utf-8")

    print(f"\nUploading to {repo_id}...")
    api.upload_folder(
        folder_path=str(EXPORT_DIR),
        repo_id=repo_id,
        repo_type="dataset",
        allow_patterns=["**/*.parquet", "README.md"],
        # No delete_patterns: CI seeds from this dataset before collecting
        # (seed_from_huggingface.py), so a table missing from one run's export
        # means something went wrong, not that it should be removed.
        commit_message=f"Update snapshot ({len(stats)} tables, {total_rows:,} rows)",
    )

    print(f"\nDone! Dataset: https://huggingface.co/datasets/{repo_id}")
    print(f"  Load with: ds = load_dataset('{repo_id}')")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload curated shipping data to HuggingFace")
    parser.add_argument("--repo-name", default="shipping-data-pipeline", help="HF repo name")
    parser.add_argument("--private", action="store_true", help="Make dataset private")
    args = parser.parse_args()
    main(repo_name=args.repo_name, private=args.private)
