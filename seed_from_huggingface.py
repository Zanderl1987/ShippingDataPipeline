#!/usr/bin/env python3
"""
Load the published HuggingFace tables into the local DuckDB before collecting.

CI starts every run with an empty database. Without this step each run would
publish only what that run collected: snapshot feeds (AIS positions, port
congestion, weather) would never accumulate history, and a source that failed
for a day would vanish from the dataset. Seeding first makes the HF dataset
the store of record -- collectors upsert on top of it, then
upload_huggingface.py publishes the merged result.

Only empty tables are seeded, so running this against a populated local
database is a no-op rather than a source of duplicates.

Usage:
    python seed_from_huggingface.py [--repo-name shipping-data-pipeline]

Exits non-zero if the dataset can't be read, so CI never publishes a
snapshot built on a partial seed.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path

from dotenv import load_dotenv
from huggingface_hub import HfApi, hf_hub_download

load_dotenv(Path(__file__).parent / ".env")

from src.config import settings  # noqa: E402  (needs env vars from load_dotenv)
from src.storage.schema import ALL_TABLES  # noqa: E402
from src.storage.writer import init_db  # noqa: E402

INTERNAL_TABLES = {"schema_migrations", "schema_versions", "source_tracking", "lineage_events"}


def main(repo_name: str = "shipping-data-pipeline") -> int:
    token = os.environ.get("HUGGINGFACE_TOKEN") or os.environ.get("HF_TOKEN") or None
    repo_id = f"ZanderL1337/{repo_name}"
    known = {t.name for t in ALL_TABLES} - INTERNAL_TABLES

    files = set(HfApi(token=token).list_repo_files(repo_id, repo_type="dataset"))
    published = sorted(t for t in known if f"{t}/{t}.parquet" in files)
    if not published:
        print(f"ERROR: no table parquet files found in {repo_id}")
        return 1

    settings.ensure_dirs()
    conn = init_db()
    total = 0
    try:
        with tempfile.TemporaryDirectory() as tmp:
            for table in published:
                existing = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                if existing and existing[0] > 0:
                    print(f"  {table}: skipped, already has {existing[0]:,} rows")
                    continue

                path = hf_hub_download(
                    repo_id,
                    f"{table}/{table}.parquet",
                    repo_type="dataset",
                    token=token,
                    local_dir=tmp,
                )
                # Insert only columns both sides have: the table may have
                # gained columns since the snapshot was exported.
                table_cols = {
                    r[0] for r in conn.execute(f'DESCRIBE "{table}"').fetchall()
                }
                file_cols = [
                    r[0]
                    for r in conn.execute(
                        "DESCRIBE SELECT * FROM read_parquet(?)", [path]
                    ).fetchall()
                ]
                cols = ", ".join(f'"{c}"' for c in file_cols if c in table_cols)
                conn.execute(
                    f'INSERT INTO "{table}" ({cols}) SELECT {cols} FROM read_parquet(?)',
                    [path],
                )
                row = conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()
                count = row[0] if row else 0
                total += count
                print(f"  {table}: seeded {count:,} rows")
                Path(path).unlink()
    finally:
        conn.close()

    print(f"Seeded {total:,} rows across {len(published)} published tables from {repo_id}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Seed local DuckDB from the HF dataset")
    parser.add_argument("--repo-name", default="shipping-data-pipeline", help="HF repo name")
    args = parser.parse_args()
    sys.exit(main(repo_name=args.repo_name))
