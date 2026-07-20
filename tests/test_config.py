from __future__ import annotations

from pathlib import Path

from src.config import Settings


def test_defaults() -> None:
    s = Settings.from_env()
    assert s.data_dir == Path("./data")
    assert s.storage_dir == Path("./storage")
    assert s.log_level == "INFO"


def test_from_env(monkeypatch) -> None:
    monkeypatch.setenv("SDP_DATA_DIR", "/tmp/test_data")
    monkeypatch.setenv("SDP_STORAGE_DIR", "/tmp/test_storage")
    monkeypatch.setenv("SDP_LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("AISSTREAM_API_KEY", "test_key_123")

    s = Settings.from_env()
    assert s.data_dir == Path("/tmp/test_data")
    assert s.storage_dir == Path("/tmp/test_storage")
    assert s.log_level == "DEBUG"
    assert s.aisstream_api_key == "test_key_123"


def test_ensure_dirs(tmp_path) -> None:
    s = Settings(
        data_dir=tmp_path / "data",
        storage_dir=tmp_path / "storage",
    )
    s.ensure_dirs()
    assert (tmp_path / "data").exists()
    assert (tmp_path / "storage").exists()
    assert (tmp_path / "storage" / "parquet").exists()
