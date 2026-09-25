"""Weekly inputs for the oil price model, read from the HF dataset.

Every weekly series ends on a Friday (``week_end``), the day Brent's weekly
close is taken. The sums over ~6M PortWatch rows run as SQL against HF, so only
a few thousand weekly totals reach this machine.
"""
from __future__ import annotations

import polars as pl

from src.ml.port_forecast.backtest import hf_connection, hf_table

#: Tanker exporters and importers, grouped as the oil market talks about them.
EXPORTERS = {
    "gulf": ["SAU", "IRQ", "ARE", "KWT", "QAT", "OMN", "IRN", "BHR"],
    "usa": ["USA"],
    "russia": ["RUS"],
    "americas": ["BRA", "GUY", "CAN", "MEX", "COL", "VEN", "ECU", "ARG"],
    "west_africa": ["NGA", "AGO", "GHA", "COG", "GAB", "GNQ", "CMR"],
    "norway_uk": ["NOR", "GBR"],
}
IMPORTERS = {
    "china": ["CHN"],
    "india": ["IND"],
    "europe": ["NLD", "DEU", "FRA", "ITA", "ESP", "BEL", "POL", "GRC", "PRT", "SWE",
               "FIN", "DNK", "GBR", "TUR"],
    "usa": ["USA"],
    "japan_korea": ["JPN", "KOR"],
}
CHOKEPOINTS = {
    "hormuz": "chokepoint6",
    "bab_el_mandeb": "chokepoint4",
    "suez": "chokepoint1",
    "cape": "chokepoint7",
    "malacca": "chokepoint5",
    "bosporus": "chokepoint3",
    "panama": "chokepoint2",
}
#: EIA weekly stocks: (product, area code, stock type) -> name.
STOCKS = {
    ("Crude Oil", "NUS", "commercial"): "crude",
    ("Crude Oil", "YCUOK", "commercial"): "cushing",
    ("Total Gasoline", "NUS", "commercial"): "gasoline",
    ("Distillate Fuel Oil", "NUS", "commercial"): "distillate",
    ("Crude Oil", "NUS", "spr"): "spr",
}

# Friday that ends the Saturday-Friday week containing d (DuckDB: isodow Fri = 5).
_WEEK_END = "({d} + INTERVAL ((5 - isodow({d}) + 7) % 7) DAY)::DATE"


def _groups_sql(groups: dict[str, list[str]]) -> str:
    return " ".join(
        f"WHEN iso3 IN ({', '.join(repr(c) for c in codes)}) THEN '{name}'"
        for name, codes in groups.items()
    )


def load_prices() -> pl.DataFrame:
    """Daily Brent and WTI closes (FRED), oldest first."""
    conn = hf_connection()
    return conn.execute(
        f"""SELECT price_date, brent_usd, wti_usd FROM read_parquet({hf_table('oil_prices')})
            WHERE source = 'fred' AND brent_usd IS NOT NULL ORDER BY price_date"""
    ).pl()


def load_flows() -> pl.DataFrame:
    """Weekly tanker tonnes: world exports and each group's exports/imports.

    Long format: ``week_end, series, value, days``. ``days`` counts the days
    reported, so a part week can be dropped.
    """
    conn = hf_connection()
    week = _WEEK_END.format(d="activity_date")
    exp_case = _groups_sql(EXPORTERS)
    imp_case = _groups_sql(IMPORTERS)
    return conn.execute(
        f"""
        WITH d AS (
            SELECT activity_date, iso3, export_tanker, import_tanker
            FROM read_parquet({hf_table('port_activity')})
        ),
        daily AS (
            SELECT activity_date, 'exp_world' AS series, sum(export_tanker) AS v FROM d GROUP BY 1
            UNION ALL
            SELECT activity_date, 'exp_' || CASE {exp_case} END, sum(export_tanker)
            FROM d WHERE CASE {exp_case} END IS NOT NULL GROUP BY 1, 2
            UNION ALL
            SELECT activity_date, 'imp_' || CASE {imp_case} END, sum(import_tanker)
            FROM d WHERE CASE {imp_case} END IS NOT NULL GROUP BY 1, 2
        )
        SELECT {week} AS week_end, series, sum(v) AS value, count(*) AS days
        FROM daily GROUP BY 1, 2 ORDER BY 1, 2
        """
    ).pl()


def load_chokepoints() -> pl.DataFrame:
    """Weekly tanker transits through the main oil chokepoints (long format)."""
    conn = hf_connection()
    week = _WEEK_END.format(d="transit_date")
    case = " ".join(f"WHEN chokepoint_id = '{cid}' THEN '{n}'" for n, cid in CHOKEPOINTS.items())
    ids = ", ".join(repr(c) for c in CHOKEPOINTS.values())
    return conn.execute(
        f"""
        SELECT {week} AS week_end, 'choke_' || CASE {case} END AS series,
               sum(n_tanker)::DOUBLE AS value, count(*) AS days
        FROM read_parquet({hf_table('chokepoint_transits')})
        WHERE chokepoint_id IN ({ids})
        GROUP BY 1, 2 ORDER BY 1, 2
        """
    ).pl()


def load_stocks() -> pl.DataFrame:
    """EIA weekly stocks (thousand barrels) for the series in ``STOCKS``."""
    conn = hf_connection()
    keys = " OR ".join(
        f"(product = '{p}' AND area_code = '{a}' AND stock_type = '{t}')" for p, a, t in STOCKS
    )
    case = " ".join(
        f"WHEN product = '{p}' AND area_code = '{a}' AND stock_type = '{t}' THEN '{n}'"
        for (p, a, t), n in STOCKS.items()
    )
    return conn.execute(
        f"""
        SELECT report_date AS week_end, 'stock_' || CASE {case} END AS series,
               value_thousand_bbl AS value
        FROM read_parquet({hf_table('oil_inventories')})
        WHERE source = 'eia_petroleum' AND frequency = 'weekly' AND unit = 'MBBL' AND ({keys})
        ORDER BY 1, 2
        """
    ).pl()
