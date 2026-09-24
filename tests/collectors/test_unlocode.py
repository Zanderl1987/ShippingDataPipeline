from __future__ import annotations

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.unlocode import (
    _build_records,
    _parse_coordinate,
    _parse_coordinates,
    collect_ports,
    download_zip,
    extract_code_lists,
    parse_code_list_rows,
    parse_code_lists,
)

# Tiny fabricated code-list pair, mirroring the real release layout: two
# comma-delimited parts, 12 positional columns, no header row. Country
# header lines carry no location component and must be skipped.
PART1 = (
    ",AD,,ANDORRA,,,,,,,,\n"
    ",AD,ALV,Andorra la Vella,Andorra la Vella,,--34-6--,AI,0601,,4230N 00131E,\n"
    ",DE,BRE,Bremen,Bremen,,1-------,AQ,9401,,5304N 00848E,\n"
)
PART2 = (
    ",NL,,NETHERLANDS,,,,,,,,\n"
    ",NL,RTM,Rotterdam,Rotterdam,,1-45---,AI,9401,,5154N 00428E,\n"
    ",US,LAX,Los Angeles,Los Angeles,CA,1-4----,AI,9401,,3356N 11824W,\n"
)

EXPECTED_COLUMNS = (
    "change",
    "country",
    "location",
    "name",
    "name_wo_diacritics",
    "subdiv",
    "function",
    "status",
    "entry_date",
    "iata",
    "coordinates",
    "remarks",
)


def make_zip_bytes() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("release/csv/UNLOCODE CodeListPart1.csv", PART1)
        zf.writestr("release/csv/UNLOCODE CodeListPart2.csv", PART2)
        zf.writestr("release/csv/SubdivisionCodes.csv", ",AD,,ANDORRA\n")
        zf.writestr("release/txt/UNLOCODE CodeList.txt", "not csv")
    return buf.getvalue()


class TestParseCoordinate:
    def test_latitude_north(self) -> None:
        assert _parse_coordinate("4230N") == pytest.approx(42.5, abs=1e-9)

    def test_longitude_east(self) -> None:
        assert _parse_coordinate("00131E") == pytest.approx(1 + 31 / 60, abs=1e-9)

    def test_south_is_negative(self) -> None:
        assert _parse_coordinate("3350S") == pytest.approx(-33 - 50 / 60, abs=1e-9)

    def test_west_is_negative(self) -> None:
        assert _parse_coordinate("11824W") == pytest.approx(-118 - 24 / 60, abs=1e-9)

    def test_placeholders(self) -> None:
        assert _parse_coordinate("--") is None
        assert _parse_coordinate("") is None
        assert _parse_coordinate(None) is None
        assert _parse_coordinate("1234X") is None


class TestParseCoordinatesPair:
    def test_pair(self) -> None:
        lat, lon = _parse_coordinates("4230N 00131E")
        assert lat == pytest.approx(42.5, abs=1e-9)
        assert lon == pytest.approx(1 + 31 / 60, abs=1e-9)

    def test_missing(self) -> None:
        assert _parse_coordinates(None) == (None, None)
        assert _parse_coordinates("") == (None, None)

    def test_out_of_range_pair_dropped(self) -> None:
        # UN/LOCODE has typos that parse to impossible values (Mironovka UA at
        # longitude 381.8); drop the pair rather than publish them.
        assert _parse_coordinates("4829N 38150E") == (None, None)
        assert _parse_coordinates("9130N 00131E") == (None, None)


class TestParseCodeListRows:
    def test_parses_positional_columns(self) -> None:
        rows = parse_code_list_rows(PART1)
        assert len(rows) == 2
        row = rows[0]
        for col in EXPECTED_COLUMNS:
            assert col in row
        assert row["country"] == "AD"
        assert row["location"] == "ALV"
        assert row["function"] == "--34-6--"
        assert row["status"] == "AI"
        assert row["coordinates"] == "4230N 00131E"

    def test_skips_country_header_lines(self) -> None:
        rows = parse_code_list_rows(PART1)
        # The bare ',AD,,ANDORRA' line has no location component.
        assert all(r["location"].strip() for r in rows)

    def test_empty_input(self) -> None:
        assert parse_code_list_rows("") == []


class TestExtractCodeLists:
    def test_finds_only_code_list_parts(self, ) -> None:
        texts = extract_code_lists(make_zip_bytes())
        assert len(texts) == 2

    def test_empty_zip(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("readme.txt", "nothing here")
        assert extract_code_lists(buf.getvalue()) == []


class TestBuildRecords:
    def test_builds_all_rows_with_function_and_status(self) -> None:
        rows = parse_code_list_rows(PART1) + parse_code_list_rows(PART2)
        records = _build_records(rows)
        by_locode = {r["unlocode"]: r for r in records}
        assert set(by_locode) == {"ADALV", "DEBRE", "NLRTM", "USLAX"}
        bre = by_locode["DEBRE"]
        assert bre["port_name"] == "Bremen"
        assert bre["country_code"] == "DE"
        # Function class '1' in first position marks a port; the string is
        # preserved so consumers can filter.
        assert bre["function_class"] == "1-------"
        assert bre["status"] == "AQ"

    def test_coordinates_converted(self) -> None:
        rows = parse_code_list_rows(PART2)
        records = {r["unlocode"]: r for r in _build_records(rows)}
        lax = records["USLAX"]
        assert lax["latitude"] == pytest.approx(33 + 56 / 60, abs=1e-9)
        assert lax["longitude"] == pytest.approx(-(118 + 24 / 60), abs=1e-9)

    def test_missing_coordinates_kept_as_none(self) -> None:
        rows = parse_code_list_rows(",FR,ABC,NoCoords,NoCoords,,1-------,AA,,,,\n")
        records = _build_records(rows)
        assert len(records) == 1
        assert records[0]["latitude"] is None
        assert records[0]["longitude"] is None

    def test_non_port_rows_kept(self) -> None:
        # ADALV has function '--34-6--' (no '1') — it stays available rather
        # than being filtered out.
        rows = parse_code_list_rows(PART1)
        locodes = [r["unlocode"] for r in _build_records(rows)]
        assert "ADALV" in locodes


class TestParseCodeLists:
    def test_end_to_end_from_zip_bytes(self) -> None:
        df = parse_code_lists(make_zip_bytes())
        assert df.height == 4
        for col in (
            "unlocode",
            "port_name",
            "country_code",
            "latitude",
            "longitude",
            "function_class",
            "status",
            "source",
        ):
            assert col in df.columns
        assert df["source"].to_list() == ["unlocode"] * 4

    def test_empty_zip_yields_zero_rows(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("empty.csv", "")
        assert parse_code_lists(buf.getvalue()).height == 0


class TestCollect:
    @patch("src.collectors.unlocode.SourceTracker")
    @patch("src.collectors.unlocode.write_raw", return_value=4)
    @patch("src.collectors.unlocode.download_zip", return_value=make_zip_bytes())
    def test_collect_writes_rows(
        self,
        mock_download: MagicMock,
        mock_write: MagicMock,
        mock_tracker_cls: MagicMock,
    ) -> None:
        count = collect_ports(mock_tracker_cls.return_value)
        assert count == 4
        mock_write.assert_called_once()
        args = mock_write.call_args.args
        assert args[0] == "unlocode"
        assert mock_write.call_args.kwargs.get("table_name") == "ports"

    @patch("src.collectors.unlocode.SourceTracker")
    @patch("src.collectors.unlocode.download_zip")
    def test_collect_empty_zip_returns_zero(
        self,
        mock_download: MagicMock,
        mock_tracker_cls: MagicMock,
    ) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("other.csv", "")
        mock_download.return_value = buf.getvalue()
        assert collect_ports(mock_tracker_cls.return_value) == 0

    @patch("src.collectors.unlocode.get_with_retry")
    def test_download_zip(self, mock_get: MagicMock) -> None:
        zip_bytes = make_zip_bytes()
        mock_resp = MagicMock()
        mock_resp.content = zip_bytes
        mock_get.return_value = mock_resp

        assert download_zip() == zip_bytes
