from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TableSchema:
    name: str
    partition_cols: list[str] = field(default_factory=list)
    raw_sql: str = ""

    def create_sql(self) -> str:
        return self.raw_sql


AIS_POSITIONS = TableSchema(
    name="ais_positions",
    partition_cols=["partition_date", "source"],
    raw_sql="""
CREATE TABLE IF NOT EXISTS ais_positions (
    mmsi            BIGINT,
    imo             BIGINT,
    vessel_name     VARCHAR,
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
    raw_sql="""
CREATE TABLE IF NOT EXISTS vessels (
    imo             BIGINT PRIMARY KEY,
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

ALL_TABLES: list[TableSchema] = [
    AIS_POSITIONS,
    VESSELS,
    PORT_CALLS,
    PORTS,
    MARINE_WEATHER,
    WEATHER,
    TRADE_FLOW,
    FREIGHT_RATES,
    SOURCE_TRACKING,
]
