"""DuckDB table schemas for the shipping pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TableSchema:
    name: str
    partition_cols: list[str] = field(default_factory=list)
    raw_sql: str = ""
    version: str = "0.1.0"
    description: str = ""
    #: Natural key identifying one row. Partitioned tables cannot carry a
    #: PRIMARY KEY (DuckDB would reject the repeated inserts a backfill needs),
    #: so `write_raw` uses these columns to replace rows a re-run resupplies
    #: instead of appending them again. Empty means append-only.
    dedup_keys: list[str] = field(default_factory=list)

    def create_sql(self) -> str:
        return self.raw_sql


AIS_POSITIONS = TableSchema(
    name="ais_positions",
    partition_cols=["partition_date", "source"],
    # Axiomancer identifies vessels by imo and reports no mmsi or timestamp at
    # all, so a (mmsi, timestamp) key would collapse its whole snapshot into one
    # row. partition_date keeps successive daily snapshots apart.
    dedup_keys=["mmsi", "imo", "timestamp", "source", "partition_date"],
    version="0.1.0",
    description="AIS vessel position reports",
    raw_sql="""
CREATE TABLE IF NOT EXISTS ais_positions (
    mmsi            BIGINT,
    imo             BIGINT,
    vessel_name     VARCHAR,
    vessel_type     VARCHAR,
    latitude        DOUBLE,
    longitude       DOUBLE,
    sog             DOUBLE,
    cog             DOUBLE,
    heading         DOUBLE,
    nav_status      VARCHAR,
    draught         DOUBLE,
    destination     VARCHAR,
    eta             VARCHAR,
    timestamp       TIMESTAMP,
    source          VARCHAR,
    partition_date  DATE,
    ingested_at     TIMESTAMP DEFAULT now()
);
""",
)

VESSELS = TableSchema(
    name="vessels",
    partition_cols=[],
    dedup_keys=["imo", "mmsi", "source"],
    version="0.1.0",
    description="Vessel identification data",
    raw_sql="""
CREATE TABLE IF NOT EXISTS vessels (
    imo             BIGINT,
    mmsi            BIGINT,
    vessel_name     VARCHAR,
    vessel_type     VARCHAR,
    flag            VARCHAR,
    callsign        VARCHAR,
    length_m        DOUBLE,
    beam_m          DOUBLE,
    gross_tonnage   DOUBLE,
    deadweight_tonnage DOUBLE,
    year_built      INTEGER,
    owner_name      VARCHAR,
    manager_name    VARCHAR,
    source          VARCHAR,
    ingested_at     TIMESTAMP DEFAULT now()
);
""",
)

PORT_CALLS = TableSchema(
    name="port_calls",
    partition_cols=["partition_date", "source"],
    dedup_keys=["mmsi", "port_unlocode", "event_type", "event_timestamp", "source"],
    version="0.1.0",
    description="Vessel port call events",
    raw_sql="""
CREATE TABLE IF NOT EXISTS port_calls (
    imo             BIGINT,
    mmsi            BIGINT,
    vessel_name     VARCHAR,
    port_unlocode   VARCHAR,
    port_name       VARCHAR,
    country         VARCHAR,
    event_type      VARCHAR,
    event_timestamp TIMESTAMP,
    eta             TIMESTAMP,
    etd             TIMESTAMP,
    previous_port   VARCHAR,
    next_port       VARCHAR,
    source          VARCHAR,
    partition_date  DATE,
    ingested_at     TIMESTAMP DEFAULT now()
);
""",
)

TRADE_FLOW = TableSchema(
    name="trade_flow",
    partition_cols=["year", "reporter_code"],
    dedup_keys=[
        "year",
        "reporter_code",
        "partner_code",
        "commodity_code",
        "flow_code",
        "source",
    ],
    version="0.1.0",
    description="International trade flow data",
    raw_sql="""
CREATE TABLE IF NOT EXISTS trade_flow (
    reporter_code   INTEGER,
    partner_code    INTEGER,
    commodity_code  VARCHAR,
    flow_code       VARCHAR,
    year            INTEGER,
    trade_value_usd DOUBLE,
    net_weight_kg   DOUBLE,
    source          VARCHAR,
    ingested_at     TIMESTAMP DEFAULT now()
);
""",
)

FREIGHT_RATES = TableSchema(
    name="freight_rates",
    partition_cols=["rate_date"],
    dedup_keys=["rate_date", "route_code", "container_type", "source"],
    version="0.1.0",
    description="Container freight rates by route",
    raw_sql="""
CREATE TABLE IF NOT EXISTS freight_rates (
    route_code      VARCHAR,
    route_name      VARCHAR,
    origin          VARCHAR,
    destination     VARCHAR,
    container_type  VARCHAR,
    rate_usd        DOUBLE,
    rate_date       DATE,
    source          VARCHAR,
    ingested_at     TIMESTAMP DEFAULT now()
);
""",
)

PORTS = TableSchema(
    name="ports",
    partition_cols=[],
    version="0.1.0",
    description="Global port reference data",
    raw_sql="""
CREATE TABLE IF NOT EXISTS ports (
    unlocode        VARCHAR PRIMARY KEY,
    port_name       VARCHAR,
    country         VARCHAR,
    country_code    VARCHAR,
    latitude        DOUBLE,
    longitude       DOUBLE,
    timezone        VARCHAR,
    region          VARCHAR,
    source          VARCHAR,
    ingested_at     TIMESTAMP DEFAULT now()
);
""",
)

MARINE_WEATHER = TableSchema(
    name="marine_weather",
    partition_cols=["partition_date", "source"],
    dedup_keys=["timestamp", "latitude", "longitude", "source"],
    version="0.1.0",
    description="Ocean and marine weather conditions",
    raw_sql="""
CREATE TABLE IF NOT EXISTS marine_weather (
    timestamp               TIMESTAMP,
    latitude                DOUBLE,
    longitude               DOUBLE,
    wave_height             DOUBLE,
    wave_direction          DOUBLE,
    wave_period             DOUBLE,
    swell_wave_height       DOUBLE,
    swell_wave_direction    DOUBLE,
    swell_wave_period       DOUBLE,
    ocean_current_velocity  DOUBLE,
    ocean_current_direction DOUBLE,
    sea_surface_temperature DOUBLE,
    source                  VARCHAR,
    partition_date          DATE,
    ingested_at             TIMESTAMP DEFAULT now()
);
""",
)

WEATHER = TableSchema(
    name="weather",
    partition_cols=["partition_date", "source"],
    dedup_keys=["timestamp", "latitude", "longitude", "source"],
    version="0.1.0",
    description="General weather observations",
    raw_sql="""
CREATE TABLE IF NOT EXISTS weather (
    timestamp       TIMESTAMP,
    latitude        DOUBLE,
    longitude       DOUBLE,
    wind_speed_10m  DOUBLE,
    wind_direction_10m DOUBLE,
    wind_gusts_10m  DOUBLE,
    pressure_msl    DOUBLE,
    temperature_2m  DOUBLE,
    precipitation   DOUBLE,
    source          VARCHAR,
    partition_date  DATE,
    ingested_at     TIMESTAMP DEFAULT now()
);
""",
)

SOURCE_TRACKING = TableSchema(
    name="source_tracking",
    partition_cols=[],
    version="0.1.0",
    description="Data source collection tracking",
    raw_sql="""
CREATE TABLE IF NOT EXISTS source_tracking (
    source          VARCHAR,
    collection_ts   TIMESTAMP,
    rows_fetched    INTEGER,
    rows_written    INTEGER,
    status          VARCHAR,
    error_message   VARCHAR,
    duration_ms     INTEGER,
    PRIMARY KEY (source, collection_ts)
);
""",
)

CHOKEPOINT_STATUS = TableSchema(
    name="chokepoint_status",
    partition_cols=["partition_date", "source"],
    dedup_keys=["partition_date", "chokepoint_id", "source"],
    version="0.1.0",
    description="Maritime chokepoint alert status",
    raw_sql="""
CREATE TABLE IF NOT EXISTS chokepoint_status (
    chokepoint_id           VARCHAR,
    chokepoint_name         VARCHAR,
    status                  VARCHAR,
    signals_last_24h        INTEGER,
    high_alerts_last_24h    INTEGER,
    signals_last_7d         INTEGER,
    latest_high_headline    VARCHAR,
    crisis_day              INTEGER,
    situation_url           VARCHAR,
    source                  VARCHAR,
    partition_date          DATE,
    ingested_at             TIMESTAMP DEFAULT now()
);
""",
)

OIL_INVENTORIES = TableSchema(
    name="oil_inventories",
    partition_cols=["report_date", "source"],
    dedup_keys=["report_date", "product", "area_code", "stock_type", "source"],
    version="0.1.0",
    description="Oil inventory stock levels",
    raw_sql="""
CREATE TABLE IF NOT EXISTS oil_inventories (
    report_date         DATE,
    product             VARCHAR,
    area                VARCHAR,
    area_code           VARCHAR,
    stock_type          VARCHAR,
    value_thousand_bbl  DOUBLE,
    unit                VARCHAR,
    frequency           VARCHAR,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
);
""",
)

CHOKEPOINT_TRANSITS = TableSchema(
    name="chokepoint_transits",
    partition_cols=["transit_date", "source"],
    dedup_keys=["transit_date", "chokepoint_id", "source"],
    version="0.1.0",
    description="Vessel transits through chokepoints",
    raw_sql="""
CREATE TABLE IF NOT EXISTS chokepoint_transits (
    transit_date            DATE,
    chokepoint_id           VARCHAR,
    chokepoint_name         VARCHAR,
    n_container             INTEGER,
    n_dry_bulk              INTEGER,
    n_general_cargo         INTEGER,
    n_roro                  INTEGER,
    n_tanker                INTEGER,
    n_cargo                 INTEGER,
    n_total                 INTEGER,
    capacity_container      DOUBLE,
    capacity_dry_bulk       DOUBLE,
    capacity_general_cargo  DOUBLE,
    capacity_roro           DOUBLE,
    capacity_tanker         DOUBLE,
    capacity_cargo          DOUBLE,
    capacity                DOUBLE,
    source                  VARCHAR,
    partition_date          DATE,
    ingested_at             TIMESTAMP DEFAULT now()
);
""",
)

OIL_PRICES = TableSchema(
    name="oil_prices",
    partition_cols=["price_date", "source"],
    dedup_keys=["price_date", "source"],
    version="0.1.0",
    description="Oil and LNG price benchmarks",
    raw_sql="""
CREATE TABLE IF NOT EXISTS oil_prices (
    price_date              DATE,
    brent_usd               DOUBLE,
    wti_usd                 DOUBLE,
    dubai_usd               DOUBLE,
    lng_jkm_mmbtu           DOUBLE,
    vlcc_td3c_ws            DOUBLE,
    vlcc_td3c_tce_usd_day   DOUBLE,
    risk_premium_pct        DOUBLE,
    td_change_pct           DOUBLE,
    source                  VARCHAR,
    partition_date          DATE,
    ingested_at             TIMESTAMP DEFAULT now()
);
""",
)

OIL_TRADE = TableSchema(
    name="oil_trade",
    partition_cols=["period", "source"],
    # Primary and secondary JODI products land in the same (period, source)
    # partition, so the key has to reach past the partition columns.
    dedup_keys=[
        "period",
        "reporting_country",
        "partner_country",
        "product_code",
        "flow",
        "unit",
        "source",
    ],
    version="0.1.0",
    description="Oil trade flows by country",
    raw_sql="""
CREATE TABLE IF NOT EXISTS oil_trade (
    period              VARCHAR,
    reporting_country   VARCHAR,
    reporting_code      INTEGER,
    partner_country     VARCHAR,
    partner_code        INTEGER,
    product             VARCHAR,
    product_code        VARCHAR,
    flow                VARCHAR,
    quantity_ktonnes    DOUBLE,
    quantity_barrels    DOUBLE,
    unit                VARCHAR,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
);
""",
)

VESSEL_REGISTRY = TableSchema(
    name="vessel_registry",
    partition_cols=[],
    dedup_keys=["imo", "mmsi", "source"],
    version="0.1.0",
    description="Vessel registry and ownership data",
    raw_sql="""
CREATE TABLE IF NOT EXISTS vessel_registry (
    imo             BIGINT,
    mmsi            BIGINT,
    vessel_name     VARCHAR,
    vessel_type     VARCHAR,
    flag            VARCHAR,
    year_built      INTEGER,
    gross_tonnage   DOUBLE,
    deadweight_tonnage DOUBLE,
    length_m        DOUBLE,
    beam_m          DOUBLE,
    source          VARCHAR,
    ingested_at     TIMESTAMP DEFAULT now()
);
""",
)

VESSEL_SAFETY = TableSchema(
    name="vessel_safety",
    partition_cols=[],
    dedup_keys=["imo", "inspection_date", "source"],
    version="0.1.0",
    description="Vessel safety inspection records",
    raw_sql="""
CREATE TABLE IF NOT EXISTS vessel_safety (
    imo             BIGINT,
    vessel_name     VARCHAR,
    flag            VARCHAR,
    vessel_type     VARCHAR,
    year_built      INTEGER,
    gross_tonnage   DOUBLE,
    deadweight_tonnage DOUBLE,
    last_port       VARCHAR,
    next_port       VARCHAR,
    inspection_date DATE,
    inspection_result VARCHAR,
    deficiency_count INTEGER,
    source          VARCHAR,
    ingested_at     TIMESTAMP DEFAULT now()
);
""",
)

SCHEMA_MIGRATIONS = TableSchema(
    name="schema_migrations",
    partition_cols=[],
    version="0.2.0",
    description="Tracks applied schema migrations",
    raw_sql="""
CREATE TABLE IF NOT EXISTS schema_migrations (
    version         VARCHAR PRIMARY KEY,
    description     VARCHAR,
    applied_at      TIMESTAMP DEFAULT now()
);
""",
)

LINEAGE_EVENTS = TableSchema(
    name="lineage_events",
    partition_cols=[],
    version="0.2.0",
    description="Data lineage events across collection, curation, analytics",
    raw_sql="""
CREATE TABLE IF NOT EXISTS lineage_events (
    event_id        INTEGER,
    event_type      VARCHAR NOT NULL,
    source          VARCHAR NOT NULL,
    started_at      TIMESTAMP NOT NULL,
    completed_at    TIMESTAMP,
    status          VARCHAR DEFAULT 'success',
    rows_input      INTEGER DEFAULT 0,
    rows_output     INTEGER DEFAULT 0,
    duration_ms     INTEGER,
    version         VARCHAR,
    config_hash     VARCHAR,
    error_message   VARCHAR,
    metadata        VARCHAR
);
""",
)

ALL_TABLES: list[TableSchema] = [
    AIS_POSITIONS,
    VESSELS,
    PORT_CALLS,
    PORTS,
    MARINE_WEATHER,
    WEATHER,
    TRADE_FLOW,
    FREIGHT_RATES,
    CHOKEPOINT_STATUS,
    OIL_INVENTORIES,
    CHOKEPOINT_TRANSITS,
    OIL_PRICES,
    OIL_TRADE,
    VESSEL_REGISTRY,
    VESSEL_SAFETY,
    SOURCE_TRACKING,
    SCHEMA_MIGRATIONS,
    LINEAGE_EVENTS,
]
