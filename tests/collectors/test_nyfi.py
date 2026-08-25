from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from src.collectors.nyfi import (
    _extract_indices,
    _parse_indices,
    _split_lane,
    _timeframe_to_date,
    fetch_index,
)


def make_index(
    code: str = "NYFI-TPE",
    name: str = "Trans-Pacific Eastbound",
    value: float = 2140.5,
    **extra: object,
) -> dict:
    entry: dict = {"code": code, "name": name, "value": value}
    entry.update(extra)
    return entry


def make_response(indices: list[dict] | None = None, **overrides: object) -> dict:
    payload: dict = {
        "timeframe": "2026-34",
        "publishDate": "2026-08-20",
        "indices": indices
        if indices is not None
        else [make_index()],
    }
    payload.update(overrides)
    return {"status": "ok", "data": payload}


class TestExtractIndices:
    def test_wrapped_shape(self) -> None:
        timeframe, publish_date, indices = _extract_indices(make_response())
        assert timeframe == "2026-34"
        assert publish_date == "2026-08-20"
        assert len(indices) == 1

    def test_flat_shape(self) -> None:
        data = make_response()["data"]
        timeframe, _, indices = _extract_indices(data)
        assert timeframe == "2026-34"
        assert len(indices) == 1

    def test_missing_indices(self) -> None:
        timeframe, publish_date, indices = _extract_indices({"timeframe": "2026-34"})
        assert timeframe == "2026-34"
        assert indices == []

    def test_non_dict_entries_dropped(self) -> None:
        _, _, indices = _extract_indices(make_response([make_index(), "garbage", 42]))
        assert len(indices) == 1

    def test_empty(self) -> None:
        assert _extract_indices({}) == (None, None, [])


class TestTimeframeToDate:
    def test_iso_week(self) -> None:
        assert _timeframe_to_date("2026-34") == date.fromisocalendar(2026, 34, 1)

    def test_compact_form(self) -> None:
        assert _timeframe_to_date("202634") == date.fromisocalendar(2026, 34, 1)

    def test_invalid(self) -> None:
        assert _timeframe_to_date("not-a-week") is None


class TestSplitLane:
    def test_dash_lane(self) -> None:
        assert _split_lane("Shanghai - Los Angeles") == ("Shanghai", "Los Angeles")

    def test_arrow_lane(self) -> None:
        assert _split_lane("Shanghai -> Rotterdam") == ("Shanghai", "Rotterdam")

    def test_no_destination(self) -> None:
        assert _split_lane("Trans-Pacific") == ("Trans-Pacific", None)

    def test_none_or_blank(self) -> None:
        assert _split_lane(None) == (None, None)
        assert _split_lane("   ") == (None, None)


class TestParseIndices:
    def test_maps_rows(self) -> None:
        df = _parse_indices(
            make_response(
                [
                    make_index(lane="Shanghai - Los Angeles"),
                    make_index(code="NYFI-AEU", name="Asia-Europe Westbound", value=1800.0),
                ]
            )
        )
        assert df.height == 2
        for col in (
            "route_code",
            "route_name",
            "origin",
            "destination",
            "container_type",
            "rate_usd",
            "rate_date",
            "source",
        ):
            assert col in df.columns
        assert df["route_code"][0] == "NYFI-TPE"
        assert df["origin"][0] == "Shanghai"
        assert df["destination"][0] == "Los Angeles"
        assert df["rate_usd"][0] == pytest.approx(2140.5, abs=1e-6)

    def test_rate_date_from_timeframe(self) -> None:
        df = _parse_indices(make_response())
        assert df["rate_date"][0] == date.fromisocalendar(2026, 34, 1)

    def test_source_is_nyfi(self) -> None:
        df = _parse_indices(make_response())
        assert df["source"].to_list() == ["nyfi"]

    def test_null_value_skipped(self) -> None:
        idx = make_index()
        idx["value"] = None
        df = _parse_indices(make_response([idx]))
        assert df.height == 0

    def test_missing_name_skipped(self) -> None:
        idx = {"value": 100.0}
        df = _parse_indices(make_response([idx]))
        assert df.height == 0

    def test_unknown_fields_tolerated(self) -> None:
        df = _parse_indices(make_response([make_index(surprise="x", unit="USD/FEU")]))
        assert df.height == 1

    def test_empty(self) -> None:
        assert _parse_indices(make_response([])).height == 0
        assert _parse_indices({}).height == 0


class TestFetch:
    @patch("src.collectors.nyfi.settings")
    @patch("src.collectors.nyfi.get_with_retry")
    def test_fetch_index(self, mock_get: MagicMock, mock_settings: MagicMock) -> None:
        mock_settings.nyshex_api_key = "test-key"
        response = make_response()
        mock_resp = MagicMock()
        mock_resp.json.return_value = response
        mock_get.return_value = mock_resp

        result = fetch_index("2026-08-17", "2026-08-24")
        assert result["data"]["timeframe"] == "2026-34"
        args, kwargs = mock_get.call_args
        assert kwargs["params"]["startDate"] == "2026-08-17"
        assert kwargs["params"]["endDate"] == "2026-08-24"
        assert kwargs["headers"]["Authorization"] == "ApiKey test-key"

    @patch("src.collectors.nyfi.settings")
    @patch("src.collectors.nyfi.get_with_retry")
    def test_fetch_index_raises(
        self, mock_get: MagicMock, mock_settings: MagicMock
    ) -> None:
        mock_settings.nyshex_api_key = "test-key"
        mock_get.side_effect = Exception("Connection failed")
        with pytest.raises(Exception, match="Connection failed"):
            fetch_index("2026-08-17", "2026-08-24")
