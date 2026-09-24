from __future__ import annotations

from unittest.mock import patch

import pytest

import src.collectors.usace_ports as usace
from src.storage.reader import query

# Live layer values, 2026-09-24.
HOUSTON = {
    "attributes": {
        "RANK": 1, "PORT": 2031.0, "TYPE": "Coastal", "PORTNAME": "Houston Port Authority, TX",
        "TOTAL": 309531236, "DOMESTIC": 80511805, "FOREIGN_": 229019431,
        "IMPORTS": 61307109, "EXPORTS": 167712322,
    },
    "centroid": {"x": -95.39247786294439, "y": 29.85762304152858},
}
DESCRIPTION = (
    "The principal port file contains USACE port codes, geographic location, names, and "
    "commodity tonnage summaries (total tons, domestic, foreign, imports and exports) for "
    "principal USACE ports for CY 2023."
)


def test_data_year_from_description() -> None:
    assert usace.data_year(DESCRIPTION) == 2023


def test_missing_year_refuses_to_guess() -> None:
    with pytest.raises(ValueError, match="CY"):
        usace.data_year("Principal ports tonnage.")


def test_parse() -> None:
    row = usace.parse([HOUSTON], 2023).row(0, named=True)
    assert row["port_code"] == "2031"
    assert (row["data_year"], row["tonnage_rank"], row["total_tons"]) == (2023, 1, 309531236.0)
    assert row["foreign_tons"] == row["import_tons"] + row["export_tons"]
    assert (round(row["latitude"], 2), round(row["longitude"], 2)) == (29.86, -95.39)


def test_collect_keeps_each_year() -> None:
    next_year = DESCRIPTION.replace("CY 2023", "CY 2024")
    with patch.object(usace, "query_all", return_value=[HOUSTON]):
        with patch.object(usace, "_fetch_description", return_value=DESCRIPTION):
            assert usace.collect_principal_ports() == 1
            usace.collect_principal_ports()  # rerun: replaces, no duplicate
        with patch.object(usace, "_fetch_description", return_value=next_year):
            usace.collect_principal_ports()
    rows = query("SELECT data_year FROM port_tonnage_us ORDER BY data_year")
    assert rows["data_year"].to_list() == [2023, 2024]
