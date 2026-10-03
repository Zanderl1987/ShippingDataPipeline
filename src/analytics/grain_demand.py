"""Corn, soybean and wheat demand: how fast the world is buying US grain this season.

Three tables, rebuilt from scratch each run:

``grain_export_pace`` -- one row per commodity per marketing-year week, from
USDA weekly export sales (``us_export_sales``), summed over destinations.
Commitments (shipped so far plus sold but not yet shipped) are the main
demand gauge: buyers book weeks or months ahead of loading, so sales lead
shipments. Each week is compared with the same week of the prior marketing
year and with the average of the five before it. ``pace_projection_mt``
divides this season's commitments by the share of the full year that had
typically been committed by this week, giving a rough full-season export
total. It assumes this year's booking rhythm matches the last five years.
Backtested over 21 seasons (median miss against the final total): corn 26%
at week 4, 16% at week 13, 8% at week 26, 3% at week 39; soybeans 18%, 13%,
8%, 2%. Simply repeating last season's total misses by 16% (corn) and 11%
(soybeans), so before about week 13 (early December) the projection is no
better than last year's number and should not be read as a forecast.

``grain_export_destinations`` -- the same weekly figures per buying
country, with the country's share of all commitments and its usual share at
this week (average of the five seasons before; a season it did not buy in
counts as 0). "UNKNOWN" is real sales to a buyer USDA has not named yet
(often China), not a total. The buyer mix is for monitoring only: it did not
improve the season forecast (src/ml/grain_forecast/README.md).

``grain_trade_monthly`` -- monthly US exports and imports from the Census
(``us_trade_products``) for corn, soybeans, soybean meal, soybean oil and wheat:
metric tons, dollars and dollars per ton. These are the official shipment
counts, about five weeks behind the month. Exports are US-origin only
(re-exports excluded), to match what USDA export sales track.

Marketing years for corn and soybeans run September to August, for wheat
June to May. A week's number in the marketing year counts 7-day blocks from
the first of the starting month, so the same number lines up across years.
Wheat is summed over its classes (HRW, HRS, SRW, White, Durum).
"""
from __future__ import annotations

import logging

import duckdb

logger = logging.getLogger(__name__)

PACE_TABLE = "grain_export_pace"
DESTINATIONS_TABLE = "grain_export_destinations"
MONTHLY_TABLE = "grain_trade_monthly"

# commodity -> first month of its US marketing year
COMMODITIES = {"Corn": 9, "Soybeans": 9, "Wheat": 6}

# product -> 4-digit HS heading (every HS10 code under it is summed)
CENSUS_PRODUCTS = {
    "corn": "1005",
    "soybeans": "1201",
    "soybean_meal": "2304",
    "soybean_oil": "1507",
    "wheat": "1001",
}

BASELINE_YEARS = 5


def _has_table(conn: duckdb.DuckDBPyConnection, name: str) -> bool:
    row = conn.execute(
        "SELECT count(*) FROM information_schema.tables WHERE table_name = ?", [name]
    ).fetchone()
    return bool(row and row[0])


def _count(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()
    return int(row[0]) if row else 0


def _pct(now: str, then: str) -> str:
    return f"round(100 * ({now} / nullif({then}, 0) - 1), 1)"


def _weekly_sales_sql() -> str:
    """us_export_sales for the tracked commodities, with marketing-year week."""
    starts = " ".join(f"WHEN '{c}' THEN {m}" for c, m in COMMODITIES.items())
    names = ", ".join(f"'{c}'" for c in COMMODITIES)
    return f"""
        SELECT *,
            CAST((week_ending - my_start) // 7 + 1 AS INTEGER) AS my_week
        FROM (
            SELECT
                week_ending, marketing_year, commodity, country,
                CAST(left(marketing_year, 4) AS INTEGER) AS my_start_year,
                make_date(CAST(left(marketing_year, 4) AS INTEGER),
                          CASE commodity {starts} END, 1) AS my_start,
                weekly_exports, accumulated_exports, outstanding_sales, net_sales,
                coalesce(total_commitments,
                         accumulated_exports + outstanding_sales) AS commitments,
                next_my_outstanding_sales
            FROM us_export_sales
            WHERE commodity IN ({names})
              AND week_ending IS NOT NULL
              AND marketing_year SIMILAR TO '[0-9]{{4}}/[0-9]{{4}}'
        )
    """


def create_grain_export_pace(conn: duckdb.DuckDBPyConnection) -> int:
    """Rebuild ``grain_export_pace``. Returns its row count."""
    conn.execute(f"""
        CREATE OR REPLACE TABLE {PACE_TABLE} AS
        WITH s AS ({_weekly_sales_sql()}),
        wk AS (
            SELECT commodity, marketing_year, my_start_year, my_week,
                max(week_ending) AS week_ending,
                sum(commitments) AS commitments_mt,
                sum(accumulated_exports) AS shipped_mt,
                sum(outstanding_sales) AS outstanding_mt,
                sum(net_sales) AS net_sales_mt,
                sum(weekly_exports) AS weekly_exports_mt,
                sum(next_my_outstanding_sales) AS next_my_outstanding_mt,
                count(DISTINCT country) AS n_destinations
            FROM s
            GROUP BY ALL
        ),
        -- A marketing year is finished once a later one has started; its
        -- last shipped total is the full-season export figure.
        final AS (
            SELECT commodity, my_start_year,
                arg_max(shipped_mt, my_week) AS final_shipped_mt
            FROM wk
            WHERE my_start_year < (SELECT max(my_start_year) FROM wk w2
                                   WHERE w2.commodity = wk.commodity)
            GROUP BY ALL
        ),
        wkf AS (
            SELECT wk.*, f.final_shipped_mt,
                wk.commitments_mt / nullif(f.final_shipped_mt, 0) AS share_committed
            FROM wk LEFT JOIN final f USING (commodity, my_start_year)
        ),
        base AS (
            SELECT cur.commodity, cur.my_start_year, cur.my_week,
                any_value(prev.commitments_mt)
                    FILTER (WHERE prev.my_start_year = cur.my_start_year - 1)
                    AS commitments_prior_year_mt,
                avg(prev.commitments_mt) AS commitments_avg5_mt,
                avg(prev.share_committed) AS share_committed_avg5,
                count(prev.commitments_mt) AS n_baseline_years
            FROM wk cur
            JOIN wkf prev
              ON prev.commodity = cur.commodity
             AND prev.my_week = cur.my_week
             AND prev.my_start_year BETWEEN cur.my_start_year - {BASELINE_YEARS}
                                        AND cur.my_start_year - 1
            GROUP BY ALL
        )
        SELECT
            w.commodity, w.marketing_year, w.my_week, w.week_ending,
            w.commitments_mt, w.shipped_mt, w.outstanding_mt,
            w.net_sales_mt, w.weekly_exports_mt,
            avg(w.net_sales_mt) OVER (
                PARTITION BY w.commodity, w.marketing_year ORDER BY w.my_week
                ROWS BETWEEN 3 PRECEDING AND CURRENT ROW) AS net_sales_4wk_avg_mt,
            w.next_my_outstanding_mt, w.n_destinations,
            b.commitments_prior_year_mt,
            {_pct("w.commitments_mt", "b.commitments_prior_year_mt")}
                AS commitments_vs_prior_year_pct,
            b.commitments_avg5_mt,
            {_pct("w.commitments_mt", "b.commitments_avg5_mt")}
                AS commitments_vs_avg5_pct,
            b.n_baseline_years,
            round(b.share_committed_avg5, 4) AS share_committed_avg5,
            w.commitments_mt / nullif(b.share_committed_avg5, 0) AS pace_projection_mt,
            w.final_shipped_mt
        FROM wkf w
        LEFT JOIN base b USING (commodity, my_start_year, my_week)
        ORDER BY w.commodity, w.week_ending
    """)
    count = _count(conn, PACE_TABLE)
    logger.info("Created %s with %d rows", PACE_TABLE, count)
    return count


def create_grain_export_destinations(conn: duckdb.DuckDBPyConnection) -> int:
    """Rebuild ``grain_export_destinations``. Returns its row count."""
    conn.execute(f"""
        CREATE OR REPLACE TABLE {DESTINATIONS_TABLE} AS
        WITH s AS ({_weekly_sales_sql()}),
        c AS (
            SELECT commodity, marketing_year, my_start_year, my_week, country,
                max(week_ending) AS week_ending,
                sum(commitments) AS commitments_mt,
                sum(accumulated_exports) AS shipped_mt,
                sum(outstanding_sales) AS outstanding_mt,
                sum(net_sales) AS net_sales_mt,
                sum(weekly_exports) AS weekly_exports_mt
            FROM s
            GROUP BY ALL
        ),
        sh AS (
            SELECT *,
                commitments_mt / nullif(sum(commitments_mt) OVER (
                    PARTITION BY commodity, marketing_year, my_week), 0) AS share
            FROM c
        ),
        -- A country's average share at this week over the 5 seasons before.
        -- USDA lists a country only once it has bought, so a season without
        -- a row counts as a share of 0.
        usual AS (
            SELECT cur.commodity, cur.my_start_year, cur.my_week, cur.country,
                coalesce(sum(prev.share), 0) / {BASELINE_YEARS} AS share_avg5
            FROM sh cur
            LEFT JOIN sh prev
              ON prev.commodity = cur.commodity
             AND prev.country = cur.country
             AND prev.my_week = cur.my_week
             AND prev.my_start_year BETWEEN cur.my_start_year - {BASELINE_YEARS}
                                        AND cur.my_start_year - 1
            WHERE cur.my_start_year - {BASELINE_YEARS} >= (
                SELECT min(my_start_year) FROM c WHERE c.commodity = cur.commodity)
            GROUP BY ALL
        )
        SELECT
            c.commodity, c.marketing_year, c.my_week, c.week_ending, c.country,
            c.commitments_mt, c.shipped_mt, c.outstanding_mt,
            c.net_sales_mt, c.weekly_exports_mt,
            round(100 * c.share, 1) AS share_of_commitments_pct,
            round(100 * u.share_avg5, 1) AS share_avg5_pct,
            p.commitments_mt AS commitments_prior_year_mt,
            {_pct("c.commitments_mt", "p.commitments_mt")}
                AS commitments_vs_prior_year_pct
        FROM sh c
        LEFT JOIN c p
          ON p.commodity = c.commodity
         AND p.country = c.country
         AND p.my_week = c.my_week
         AND p.my_start_year = c.my_start_year - 1
        LEFT JOIN usual u
          ON u.commodity = c.commodity
         AND u.my_start_year = c.my_start_year
         AND u.my_week = c.my_week
         AND u.country = c.country
        ORDER BY c.commodity, c.week_ending, c.commitments_mt DESC
    """)
    count = _count(conn, DESTINATIONS_TABLE)
    logger.info("Created %s with %d rows", DESTINATIONS_TABLE, count)
    return count


def create_grain_trade_monthly(conn: duckdb.DuckDBPyConnection) -> int:
    """Rebuild ``grain_trade_monthly``. Returns its row count."""
    products = " ".join(f"WHEN '{h}' THEN '{p}'" for p, h in CENSUS_PRODUCTS.items())
    headings = ", ".join(f"'{h}'" for h in CENSUS_PRODUCTS.values())
    conn.execute(f"""
        CREATE OR REPLACE TABLE {MONTHLY_TABLE} AS
        WITH m AS (
            SELECT
                CASE left(commodity_code, 4) {products} END AS product,
                CASE flow_code WHEN 'X' THEN 'export' ELSE 'import' END AS flow,
                period_date,
                sum(value_usd) AS value_usd,
                -- Bulk lines report metric tons, seed and small lots kilograms.
                sum(CASE unit_1 WHEN 'T' THEN quantity_1
                                WHEN 'KG' THEN quantity_1 / 1000 END) AS quantity_mt
            FROM us_trade_products
            WHERE left(commodity_code, 4) IN ({headings})
              AND length(commodity_code) = 10
              AND (flow_code = 'M' OR export_origin = 'domestic')
            GROUP BY ALL
        ),
        y AS (
            SELECT *, year(period_date) AS yr, month(period_date) AS mo FROM m
        ),
        b AS (
            SELECT cur.product, cur.flow, cur.period_date,
                any_value(prev.quantity_mt) FILTER (WHERE prev.yr = cur.yr - 1)
                    AS quantity_prior_year_mt,
                any_value(prev.value_usd / nullif(prev.quantity_mt, 0))
                    FILTER (WHERE prev.yr = cur.yr - 1) AS usd_per_mt_prior_year,
                avg(prev.quantity_mt) AS quantity_avg5_mt
            FROM y cur
            JOIN y prev
              ON prev.product = cur.product AND prev.flow = cur.flow
             AND prev.mo = cur.mo
             AND prev.yr BETWEEN cur.yr - {BASELINE_YEARS} AND cur.yr - 1
            GROUP BY ALL
        )
        SELECT
            y.product, y.flow, y.period_date,
            y.quantity_mt, y.value_usd,
            round(y.value_usd / nullif(y.quantity_mt, 0), 2) AS usd_per_mt,
            b.quantity_prior_year_mt,
            {_pct("y.quantity_mt", "b.quantity_prior_year_mt")}
                AS quantity_vs_prior_year_pct,
            b.quantity_avg5_mt,
            {_pct("y.quantity_mt", "b.quantity_avg5_mt")} AS quantity_vs_avg5_pct,
            round(b.usd_per_mt_prior_year, 2) AS usd_per_mt_prior_year
        FROM y
        LEFT JOIN b USING (product, flow, period_date)
        ORDER BY y.product, y.flow, y.period_date
    """)
    count = _count(conn, MONTHLY_TABLE)
    logger.info("Created %s with %d rows", MONTHLY_TABLE, count)
    return count


def create_grain_demand(conn: duckdb.DuckDBPyConnection) -> dict[str, int]:
    """Rebuild whichever grain tables have their inputs. Returns row counts."""
    results: dict[str, int] = {}
    if _has_table(conn, "us_export_sales"):
        results[PACE_TABLE] = create_grain_export_pace(conn)
        results[DESTINATIONS_TABLE] = create_grain_export_destinations(conn)
    if _has_table(conn, "us_trade_products"):
        results[MONTHLY_TABLE] = create_grain_trade_monthly(conn)
    return results
