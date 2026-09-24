from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import polars as pl
import pytest

import src.collectors.portwatch_ports as pw
from src.storage.reader import query
from src.storage.writer import init_db


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


def _activity_feature(day: str, port: str = "port1114", calls: int = 79) -> dict:
    attrs = {
        "date": day,
        "portid": port,
        "portname": "Rotterdam",
        "country": "Netherlands",
        "ISO3": "NLD",
        "portcalls": calls,
        "import": 310511.0,
        "export": 298495.0,
    }
    for v in ("container", "dry_bulk", "general_cargo", "roro", "tanker", "cargo"):
        attrs[f"portcalls_{v}"] = 1
        attrs[f"import_{v}"] = 10.5
        attrs[f"export_{v}"] = 20.5
    return {"attributes": attrs}


def _response(features: list[dict], more: bool = False) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = {"features": features, "exceededTransferLimit": more}
    return resp


class TestQueryAll:
    def test_pages_until_transfer_limit_clears(self):
        pages = [
            _response([_activity_feature("2026-09-17")], more=True),
            _response([_activity_feature("2026-09-18")], more=False),
        ]
        with patch.object(pw, "get_with_retry", side_effect=pages) as get:
            features = pw.query_all(pw.DAILY_PORTS_URL)
        assert len(features) == 2
        offsets = [c.kwargs["params"]["resultOffset"] for c in get.call_args_list]
        assert offsets == [0, 1]
        assert get.call_args_list[0].kwargs["params"]["orderByFields"] == "ObjectId"

    def test_arcgis_error_body_raises(self):
        resp = MagicMock()
        resp.json.return_value = {"error": {"code": 400, "message": "Invalid query"}}
        with patch.object(pw, "get_with_retry", return_value=resp):
            with pytest.raises(RuntimeError, match="Invalid query"):
                pw.query_all(pw.DAILY_PORTS_URL, where="bogus")


class TestDateParsing:
    def test_date_formats(self):
        assert pw._to_date("2026-09-18") == date(2026, 9, 18)
        assert pw._to_date("2019/01/04") == date(2019, 1, 4)
        assert pw._to_date(1786744701000) == date(2026, 8, 14)
        assert pw._to_date(None) is None
        assert pw._to_date("not a date") is None

    def test_datetime_offset_normalised_to_utc(self):
        assert pw._to_datetime("2024-08-30T02:00:00+02:00") == datetime(2024, 8, 30, 0, 0)
        assert pw._to_datetime(1786744701000) == datetime(2026, 8, 14, 21, 58, 21)


class TestParsePortActivity:
    def test_renames_and_types(self):
        df = pw._parse_port_activity([_activity_feature("2026-09-18")])
        row = df.row(0, named=True)
        assert row["activity_date"] == date(2026, 9, 18)
        assert row["year"] == 2026
        assert row["port_id"] == "port1114"
        assert row["iso3"] == "NLD"
        assert row["portcalls"] == 79
        assert row["import_total"] == 310511.0
        assert row["export_tanker"] == 20.5
        assert row["source"] == pw.SOURCE_ACTIVITY
        assert df.schema["portcalls"] == pl.Int32

    def test_drops_rows_without_date_or_port(self):
        bad_date = _activity_feature("2026-09-18")
        bad_date["attributes"]["date"] = None
        no_port = _activity_feature("2026-09-18")
        no_port["attributes"]["portid"] = None
        df = pw._parse_port_activity([bad_date, no_port, _activity_feature("2026-09-18")])
        assert df.height == 1

    def test_empty(self):
        assert pw._parse_port_activity([]).height == 0


class TestReadActivityCsv:
    def test_bom_and_slash_dates(self, tmp_path: Path):
        cols = ["date", "year", "month", "day", "portid", "portname", "country", "ISO3"]
        numeric = [src for _, src, _ in pw._ACTIVITY_FIELDS if src not in cols]
        header = ",".join(cols + numeric + ["ObjectId"])
        values = ",".join(["2019/01/04", "2019", "1", "4", "port1325", "Tsuruga", "Japan", "JPN"]
                          + ["2"] * len(numeric) + ["5"])
        path = tmp_path / "ports.csv"
        path.write_text("\ufeff" + header + "\n" + values + "\n", encoding="utf-8")

        df = pw._read_activity_csv(path).collect()
        row = df.row(0, named=True)
        assert row["activity_date"] == date(2019, 1, 4)
        assert row["port_id"] == "port1325"
        assert row["portcalls"] == 2
        assert row["import_total"] == 2.0
        assert row["year"] == 2019


class TestCollectPortActivity:
    def test_first_run_without_backfill_uses_lookback(self, db):
        with patch.object(pw, "query_all", return_value=[_activity_feature("2026-09-18")]) as q, \
             patch.object(pw, "_backfill_activity_from_csv") as backfill:
            written = pw.collect_port_activity(bulk_backfill=False)
        backfill.assert_not_called()
        start = date.today() - timedelta(days=pw.INITIAL_LOOKBACK_DAYS)
        assert q.call_args.kwargs["where"] == f"date >= DATE '{start.isoformat()}'"
        assert written == 1

    def test_first_run_with_backfill_then_tops_up(self, db):
        def fake_backfill():
            pw.write_raw(
                pw.SOURCE_ACTIVITY,
                pw._parse_port_activity([_activity_feature("2026-08-01")]),
                table_name="port_activity",
            )
            return 1, 1

        with patch.object(pw, "_backfill_activity_from_csv", side_effect=fake_backfill), \
             patch.object(pw, "query_all", return_value=[_activity_feature("2026-09-18")]) as q:
            written = pw.collect_port_activity(bulk_backfill=True)

        start = date(2026, 8, 1) - timedelta(days=pw.REVISION_WINDOW_DAYS)
        assert q.call_args.kwargs["where"] == f"date >= DATE '{start.isoformat()}'"
        assert written == 2
        assert query("SELECT COUNT(*) AS n FROM port_activity")["n"][0] == 2

    def test_rerun_replaces_revised_rows(self, db):
        first = [_activity_feature("2026-09-18", calls=79)]
        revised = [_activity_feature("2026-09-18", calls=81)]
        with patch.object(pw, "query_all", return_value=first):
            pw.collect_port_activity(bulk_backfill=False)
        with patch.object(pw, "query_all", return_value=revised), \
             patch.object(pw, "_backfill_activity_from_csv") as backfill:
            pw.collect_port_activity(bulk_backfill=True)
        backfill.assert_not_called()  # table is no longer empty
        result = query("SELECT portcalls FROM port_activity")
        assert result["portcalls"].to_list() == [81]


class TestPortProfiles:
    def test_parse_and_upsert(self, db):
        feature = {"attributes": {
            "portid": "port98", "portname": "Bajo Grande", "fullname": "Bajo Grande, Venezuela",
            "country": "Venezuela", "ISO3": "VEN", "continent": "South America",
            "LOCODE": "VE BJV", "lat": 10.45, "lon": -71.58, "vessel_count_total": 12,
            "vessel_count_RoRo": 5, "industry_top1": "Mineral Products",
            "share_country_maritime_import": 0.01,
        }}
        with patch.object(pw, "query_all", return_value=[feature]):
            assert pw.collect_port_profiles() == 1
            pw.collect_port_profiles()
        rows = query("SELECT * FROM port_profiles")
        assert rows.height == 1
        assert rows["vessel_count_roro"][0] == 5
        assert rows["locode"][0] == "VE BJV"


class TestChokepointProfiles:
    def test_parse_and_upsert(self, db):
        # Live layer fields, 2026-09-24.
        feature = {"attributes": {
            "portid": "chokepoint1", "portname": "Suez Canal", "fullname": "Suez Canal",
            "country": None, "LOCODE": None, "lat": 30.59334599, "lon": 32.43688221,
            "vessel_count_total": 19787, "vessel_count_RoRo": 766,
            "industry_top1": "Mineral Products",
        }}
        with patch.object(pw, "query_all", return_value=[feature]):
            assert pw.collect_chokepoint_profiles() == 1
            pw.collect_chokepoint_profiles()
        rows = query("SELECT * FROM chokepoint_profiles")
        assert rows.height == 1
        row = rows.row(0, named=True)
        assert (row["chokepoint_id"], row["chokepoint_name"]) == ("chokepoint1", "Suez Canal")
        assert (row["latitude"], row["vessel_count_roro"]) == (30.59334599, 766)


class TestTradeNowcast:
    def test_parse(self):
        df = pw._parse_tradenow([{"attributes": {
            "region": "World", "ISO3": "WLD", "date": "2026-08-01",
            "trade_value": 1.5e12, "ais_import_tanker": 3.0,
        }}])
        row = df.row(0, named=True)
        assert row["month_date"] == date(2026, 8, 1)
        assert row["iso3"] == "WLD"
        assert row["trade_value"] == 1.5e12
        assert row["ais_export_roro"] is None


class TestDisruptionEvents:
    def test_parse_disruptions_free_text_population(self):
        df = pw._parse_disruptions([{"attributes": {
            "eventid": 1558059, "eventtype": "EQ", "eventname": "Earthquake in Indonesia",
            "htmldescription": "<p>Red M 7.7 <b>Earthquake</b></p>", "alertlevel": "RED",
            "fromdate": 1786744701000, "affectedports": "port325", "n_affectedports": 1,
            "affectedpopulation": "1.9 million  in Category 1 or higher",
            "lat": -1.0, "long": 120.0,
        }}])
        row = df.row(0, named=True)
        assert row["event_id"] == "1558059"
        assert row["description"] == "Red M 7.7 Earthquake"
        assert row["affected_population"] == "1.9 million  in Category 1 or higher"
        assert row["from_date"] == datetime(2026, 8, 14, 21, 58, 21)
        assert row["source"] == pw.SOURCE_DISRUPTIONS

    def test_parse_geopulse(self):
        df = pw._parse_geopulse([{
            "attributes": {
                "eventid": 1001089, "episodeid": 11, "eventtype": "TC", "eventname": "ASNA-24",
                "alertlevel": "Orange", "alertscore": 2, "iscurrent": 0,
                "fromdate": "2024-08-30T00:00:00+00:00", "iso3": "IND",
                "severitydata": '{"severitytext": "Tropical Storm", "severity": 83.3}',
            },
            "geometry": {"x": 67.0, "y": 23.5},
        }])
        row = df.row(0, named=True)
        assert row["episode_id"] == "11"
        assert row["severity_text"] == "Tropical Storm"
        assert row["is_current"] is False
        assert (row["latitude"], row["longitude"]) == (23.5, 67.0)

    def test_collect_writes_both_sources(self, db):
        disruption = {"attributes": {"eventid": 1, "fromdate": 1786744701000}}
        geopulse = {"attributes": {"eventid": 1, "episodeid": 2, "fromdate": "2024-08-30"}}
        with patch.object(pw, "query_all", side_effect=[[disruption], [geopulse]]):
            assert pw.collect_disruption_events() == 2
        sources = query("SELECT source FROM disruption_events ORDER BY source")["source"].to_list()
        assert sources == [pw.SOURCE_DISRUPTIONS, pw.SOURCE_GEOPULSE]
