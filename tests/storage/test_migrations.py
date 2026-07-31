"""A migration that fails must not be recorded as applied.

The runner used to log per-statement failures at debug level and insert the
schema_migrations row anyway, so a failed ALTER looked permanently successful
and would never be retried. That is how ais_positions.vessel_type drifted.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.storage import migrations as m
from src.storage.writer import get_connection, init_db


@pytest.fixture
def db(tmp_path: Path):
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    init_db().close()

    yield

    settings.data_dir = old_data
    settings.storage_dir = old_storage


def _applied() -> set[str]:
    conn = get_connection()
    try:
        return m.get_applied_versions(conn)
    finally:
        conn.close()


class TestFailedMigrationIsNotRecorded:
    def test_broken_sql_is_not_marked_applied(self, db, monkeypatch) -> None:
        broken = m.Migration(
            version="99999999999",
            description="Intentionally broken",
            up_sql="ALTER TABLE table_that_does_not_exist ADD COLUMN x VARCHAR;",
            down_sql="",
        )
        monkeypatch.setattr(m, "MIGRATIONS", [broken])

        applied = m.apply_pending_migrations()

        assert applied == []
        assert "99999999999" not in _applied()

    def test_failure_is_logged_at_error(self, db, monkeypatch, caplog) -> None:
        broken = m.Migration(
            version="99999999998",
            description="Intentionally broken",
            up_sql="THIS IS NOT SQL;",
            down_sql="",
        )
        monkeypatch.setattr(m, "MIGRATIONS", [broken])

        with caplog.at_level("ERROR"):
            m.apply_pending_migrations()

        assert any(r.levelname == "ERROR" for r in caplog.records)

    def test_later_migrations_do_not_run_after_a_failure(self, db, monkeypatch) -> None:
        broken = m.Migration(
            version="99999999996",
            description="Intentionally broken",
            up_sql="ALTER TABLE nope ADD COLUMN x VARCHAR;",
            down_sql="",
        )
        following = m.Migration(
            version="99999999997",
            description="Should never run",
            up_sql="ALTER TABLE vessels ADD COLUMN IF NOT EXISTS canary VARCHAR;",
            down_sql="",
        )
        monkeypatch.setattr(m, "MIGRATIONS", [broken, following])

        m.apply_pending_migrations()

        assert _applied().isdisjoint({"99999999996", "99999999997"})
        conn = get_connection()
        try:
            cols = {
                r[0]
                for r in conn.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = 'vessels'"
                ).fetchall()
            }
        finally:
            conn.close()
        assert "canary" not in cols

    def test_a_failed_migration_is_retried_next_run(self, db, monkeypatch) -> None:
        """The point of not recording it: a fixed migration must still apply."""
        broken = m.Migration(
            version="99999999995",
            description="Broken then fixed",
            up_sql="ALTER TABLE nope ADD COLUMN x VARCHAR;",
            down_sql="",
        )
        monkeypatch.setattr(m, "MIGRATIONS", [broken])
        assert m.apply_pending_migrations() == []

        fixed = m.Migration(
            version="99999999995",
            description="Broken then fixed",
            up_sql="ALTER TABLE vessels ADD COLUMN IF NOT EXISTS repaired VARCHAR;",
            down_sql="",
        )
        monkeypatch.setattr(m, "MIGRATIONS", [fixed])
        assert m.apply_pending_migrations() == ["99999999995"]
        assert "99999999995" in _applied()


class TestGoodMigrationsStillApply:
    def test_real_registry_applies_cleanly(self, db) -> None:
        # init_db already ran them; a second pass must be a no-op, not a failure.
        assert m.apply_pending_migrations() == []
        assert {mig.version for mig in m.MIGRATIONS} <= _applied()
