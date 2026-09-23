from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import patch

import duckdb
import pytest

import seed_from_huggingface as seed
from src.storage.reader import query


@pytest.fixture
def storage(tmp_path: Path):
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"

    yield tmp_path

    settings.data_dir = old_data
    settings.storage_dir = old_storage


def _published_parquet(tmp_path: Path) -> Path:
    """A chokepoint_transits snapshot as upload_huggingface.py exports it,
    plus a column the current schema doesn't have."""
    path = tmp_path / "hf" / "chokepoint_transits.parquet"
    path.parent.mkdir()
    duckdb.execute(
        "COPY (SELECT DATE '2026-01-01' AS transit_date, 'chokepoint1' AS chokepoint_id, "
        "10 AS n_total, 'imf_portwatch' AS source, 'gone' AS retired_column) TO ? (FORMAT PARQUET)",
        [str(path)],
    )
    return path


def test_seeds_empty_tables_and_skips_populated(storage: Path):
    parquet = _published_parquet(storage)
    files = ["README.md", "chokepoint_transits/chokepoint_transits.parquet"]

    def fake_download(repo_id, filename, repo_type, token, local_dir):
        copy = Path(local_dir) / Path(filename).name
        copy.write_bytes(parquet.read_bytes())
        return str(copy)

    with patch.object(seed, "HfApi") as api, \
         patch.object(seed, "hf_hub_download", side_effect=fake_download) as download:
        api.return_value.list_repo_files.return_value = files
        assert seed.main() == 0
        assert seed.main() == 0  # second run must not duplicate

    assert download.call_count == 1
    rows = query("SELECT transit_date, n_total FROM chokepoint_transits")
    assert rows.height == 1
    assert rows["transit_date"][0] == date(2026, 1, 1)


def test_fails_when_nothing_published(storage: Path):
    with patch.object(seed, "HfApi") as api:
        api.return_value.list_repo_files.return_value = ["README.md"]
        assert seed.main() == 1
