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
from datetime import UTC, datetime
from pathlib import Path

import duckdb
from dotenv import load_dotenv
from huggingface_hub import HfApi, login

load_dotenv(Path(__file__).parent / ".env")

from src.config import settings  # noqa: E402  (needs env vars from load_dotenv)

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
  - 1MB<n<100MB
---

# Shipping Data Pipeline — Full Curated Snapshot

Maritime trade, vessel tracking, and chokepoint transit data covering **{n_tables} tables**
and **{n_rows:,} rows**.

## Data Sources

| Table | Rows | Description |
|---|---|---|
{table_rows}

## Usage

```python
from datasets import load_dataset

ds = load_dataset("{repo_id}", trust_remote_code=True)
df = ds["{first_table}"].to_pandas()
```

Or load individual parquet files directly:

```python
import pandas as pd

df = pd.read_parquet("path/to/parquet/file.parquet")
```

## Build Info

- **Generated**: {generated_date}
- **Pipeline**: ShippingDataPipeline (https://github.com/Zanderl1987/ShippingDataPipeline)
- **Tables**: {n_tables}
- **Total Rows**: {n_rows:,}
- **Total Size**: {total_size_mb:.1f} MB

## License

CC BY 4.0 — data sourced from public APIs and government/intergovernmental databases.
"""


def export_tables(db_path: Path) -> list[tuple[str, int, int]]:
    """Export each non-empty, non-internal DuckDB table to a parquet file.

    Returns a list of (table_name, row_count, file_size_bytes).
    """
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in EXPORT_DIR.glob("*.parquet"):
        stale.unlink()

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
            out_path = EXPORT_DIR / f"{name}.parquet"
            conn.execute(
                f'COPY (SELECT * FROM "{name}") TO ? (FORMAT PARQUET)',
                [str(out_path)],
            )
            stats.append((name, count, out_path.stat().st_size))
        return stats
    finally:
        conn.close()


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

    table_rows = "\n".join(f"| {name} | {count:,} | |" for name, count, _ in stats)
    readme = README_TEMPLATE.format(
        repo_id=repo_id,
        n_tables=len(stats),
        n_rows=total_rows,
        total_size_mb=total_size_mb,
        generated_date=datetime.now(UTC).strftime("%Y-%m-%d"),
        first_table=stats[0][0],
        table_rows=table_rows,
    )
    (EXPORT_DIR / "README.md").write_text(readme, encoding="utf-8")

    print(f"\nUploading to {repo_id}...")
    api.upload_folder(
        folder_path=str(EXPORT_DIR),
        repo_id=repo_id,
        repo_type="dataset",
        allow_patterns=["*.parquet", "README.md"],
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
