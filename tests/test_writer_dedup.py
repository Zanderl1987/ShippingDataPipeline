"""Re-running a collector must not duplicate rows.

Partitioned tables carry no PRIMARY KEY, so `INSERT OR REPLACE` is unavailable;
`write_raw` dedups on the natural key declared in `TableSchema.dedup_keys`.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

import polars as pl
import pytest

from src.storage.reader import query
from src.storage.writer import init_db, write_raw


@pytest.fixture
def db(tmp_path: Path):
    from src.config import settings

    old_data = settings.data_dir
    old_storage = settings.storage_dir
    settings.data_dir = tmp_path / "data"
    settings.storage_dir = tmp_path / "storage"
    settings.ensure_dirs()
    init_db().close()

    yield settings

    settings.data_dir = old_data
    settings.storage_dir = old_storage


def _transits(n_total: int = 10) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "transit_date": [date(2026, 1, 1), date(2026, 1, 1)],
            "chokepoint_id": ["chokepoint1", "chokepoint2"],
            "chokepoint_name": ["Suez Canal", "Panama Canal"],
            "n_total": [n_total, n_total],
            "source": ["imf_portwatch", "imf_portwatch"],
            "partition_date": [date(2026, 1, 1), date(2026, 1, 1)],
        }
    )


class TestPartitionedDedup:
    def test_rerun_does_not_duplicate(self, db) -> None:
        write_raw("imf_portwatch", _transits(), table_name="chokepoint_transits")
        write_raw("imf_portwatch", _transits(), table_name="chokepoint_transits")

        assert query("SELECT * FROM chokepoint_transits").height == 2

    def test_rerun_takes_the_newer_value(self, db) -> None:
        write_raw("imf_portwatch", _transits(10), table_name="chokepoint_transits")
        write_raw("imf_portwatch", _transits(42), table_name="chokepoint_transits")

        result = query("SELECT n_total FROM chokepoint_transits")
        assert result["n_total"].to_list() == [42, 42]

    def test_duplicates_within_one_batch_collapse(self, db) -> None:
        df = pl.concat([_transits(1), _transits(2)])
        count = write_raw("imf_portwatch", df, table_name="chokepoint_transits")

        assert count == 2
        assert query("SELECT * FROM chokepoint_transits").height == 2

    def test_other_partitions_untouched(self, db) -> None:
        write_raw("imf_portwatch", _transits(), table_name="chokepoint_transits")

        other = _transits().with_columns(
            pl.lit(date(2026, 1, 2)).alias("transit_date"),
            pl.lit(date(2026, 1, 2)).alias("partition_date"),
        )
        write_raw("imf_portwatch", other, table_name="chokepoint_transits")

        assert query("SELECT * FROM chokepoint_transits").height == 4

    def test_ingested_at_default_survives(self, db) -> None:
        """CREATE OR REPLACE TABLE ... AS SELECT silently drops column defaults.

        Dedup must not rebuild the table, so `ingested_at DEFAULT now()` has to
        still populate after a re-run.
        """
        write_raw("imf_portwatch", _transits(), table_name="chokepoint_transits")
        write_raw("imf_portwatch", _transits(), table_name="chokepoint_transits")

        result = query("SELECT ingested_at FROM chokepoint_transits")
        assert result.height == 2
        assert result["ingested_at"].null_count() == 0


class TestParquetStaysInSyncWithDuckDB:
    def _parquet(self, settings, source: str) -> pl.DataFrame:
        base = Path(settings.storage_dir) / "parquet" / "raw" / source
        return pl.read_parquet(str(base), hive_partitioning=True)

    def test_rerun_leaves_parquet_matching_duckdb(self, db) -> None:
        write_raw("imf_portwatch", _transits(), table_name="chokepoint_transits")
        write_raw("imf_portwatch", _transits(), table_name="chokepoint_transits")

        assert self._parquet(db, "imf_portwatch").height == 2

    def test_second_batch_in_same_partition_is_merged_not_replaced(self, db) -> None:
        """JODI writes primary then secondary products into the same
        (period, source) partition. Polars overwrites a partition directory
        wholesale, so the second write must carry the first write's rows.
        """
        def oil(product_code: str, qty: float) -> pl.DataFrame:
            return pl.DataFrame(
                {
                    "period": ["2024-01"],
                    "reporting_country": ["AE"],
                    "partner_country": [""],
                    "product": [product_code],
                    "product_code": [product_code],
                    "flow": ["Production"],
                    "quantity_ktonnes": [qty],
                    "unit": ["KTONS"],
                    "source": ["jodi_oil"],
                    "partition_date": [date(2026, 1, 1)],
                }
            )

        write_raw("jodi_oil", oil("CRUDEOIL", 1.0), table_name="oil_trade")
        write_raw("jodi_oil", oil("GASOLINE", 2.0), table_name="oil_trade")

        assert query("SELECT * FROM oil_trade").height == 2
        assert self._parquet(db, "jodi_oil").height == 2


class TestKeyColumnsTheSourceOmits:
    def test_batch_without_mmsi_still_dedups(self, db) -> None:
        """Axiomancer sends imo but no mmsi column at all.

        The omitted column is NULL once stored, so it must be matched as NULL
        rather than disabling dedup — otherwise every run appends 59k rows.
        """
        df = pl.DataFrame(
            {
                "imo": [9995428, 9994369],
                "vessel_name": ["CAPE ANDIAMO", "TYRFING"],
                "latitude": [1.0, 2.0],
                "longitude": [3.0, 4.0],
                "source": ["axiomancer", "axiomancer"],
                "partition_date": [date(2026, 1, 1), date(2026, 1, 1)],
            }
        )
        write_raw("axiomancer", df)
        write_raw("axiomancer", df)

        assert query("SELECT * FROM ais_positions").height == 2

    def test_next_day_snapshot_is_kept_separately(self, db) -> None:
        def snapshot(day: date) -> pl.DataFrame:
            return pl.DataFrame(
                {
                    "imo": [9995428],
                    "vessel_name": ["CAPE ANDIAMO"],
                    "latitude": [1.0],
                    "longitude": [3.0],
                    "source": ["axiomancer"],
                    "partition_date": [day],
                }
            )

        write_raw("axiomancer", snapshot(date(2026, 1, 1)))
        write_raw("axiomancer", snapshot(date(2026, 1, 2)))

        assert query("SELECT * FROM ais_positions").height == 2


class TestDedupKeysAreWellFormed:
    def test_every_key_column_exists_in_its_table(self, db) -> None:
        from src.storage.schema import ALL_TABLES

        for table in ALL_TABLES:
            if not table.dedup_keys:
                continue
            cols = query(
                "SELECT column_name FROM information_schema.columns "
                f"WHERE table_name = '{table.name}'"
            )["column_name"].to_list()
            missing = [k for k in table.dedup_keys if k not in cols]
            assert not missing, f"{table.name} dedup_keys not in table: {missing}"

    def test_keyed_tables_are_not_also_primary_keyed(self, db) -> None:
        """A PRIMARY KEY table already upserts; two mechanisms would disagree."""
        from src.storage.schema import ALL_TABLES

        for table in ALL_TABLES:
            if not table.dedup_keys:
                continue
            assert "PRIMARY KEY" not in table.raw_sql, table.name


class TestUnkeyedTablesStillAppend:
    def test_lineage_events_appends(self, db) -> None:
        df = pl.DataFrame(
            {
                "event_id": [1],
                "event_type": ["collect"],
                "source": ["test"],
                "started_at": [datetime(2026, 1, 1, 0, 0, 0)],
                "status": ["success"],
            }
        )
        write_raw("test", df, table_name="lineage_events")
        write_raw("test", df, table_name="lineage_events")

        assert query("SELECT * FROM lineage_events").height == 2
