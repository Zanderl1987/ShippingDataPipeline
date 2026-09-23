from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def isolated_storage(tmp_path: Path):
    """Point every test at a throwaway data/storage directory.

    Collectors default to a SourceTracker on `settings.storage_dir`, so tests
    that mock only the fetch/write still open pipeline.db. Without this they
    wrote tracking rows into the developer's real database, and failed in CI,
    where `storage/` doesn't exist on a fresh checkout.
    """
    from src.config import settings
    from src.storage.writer import init_db

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    init_db().close()

    yield

    settings.data_dir = old_data
    settings.storage_dir = old_storage
