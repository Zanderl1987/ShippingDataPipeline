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
    # row. partition_date keeps successive daily snapshots apart. Its imo values
    # are also not unique -- imo 30 covers two unrelated vessels -- so
    # vessel_name is carried too, which keeps 3 of the 5 collisions in a 59k
    # snapshot apart. Position deliberately is NOT in the key: this is a live
    # feed, and vessels move between two calls seconds apart, so including it
    # loses idempotency entirely. One row per vessel per day is the intent.
    dedup_keys=[
        "mmsi",
        "imo",
        "vessel_name",
        "timestamp",
        "source",
        "partition_date",
    ],
    version="0.1.0",
    description="AIS vessel position reports",
    raw_sql="""
CREATE TABLE IF NOT EXISTS ais_positions (
    mmsi            BIGINT,
    imo             BIGINT,
    vessel_name     VARCHAR,
    vessel_type     VARCHAR,
    flag            VARCHAR,
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
    description=(
        "International trade flow data (UN Comtrade, Eurostat Comext, US Census) -- "
        "check the currency column, values are not all USD"
    ),
    raw_sql="""
CREATE TABLE IF NOT EXISTS trade_flow (
    reporter_code   VARCHAR,
    partner_code    VARCHAR,
    commodity_code  VARCHAR,
    flow_code       VARCHAR,
    year            INTEGER,
    trade_value_usd DOUBLE,
    net_weight_kg   DOUBLE,
    currency        VARCHAR DEFAULT 'USD',
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
    function_class  VARCHAR,
    status          VARCHAR,
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
    # EIA publishes the same measurement in two units (MBBL and MBBL/D) as
    # separate rows, so unit has to be in the key or one of them is discarded.
    dedup_keys=["report_date", "product", "area_code", "stock_type", "unit", "source"],
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

PORT_VOLUMES = TableSchema(
    name="port_volumes",
    partition_cols=["partition_date", "source"],
    dedup_keys=["port_code", "period_date", "source"],
    version="0.1.0",
    description="Monthly container throughput by port (TEU)",
    raw_sql="""
CREATE TABLE IF NOT EXISTS port_volumes (
    period              VARCHAR,
    period_date         DATE,
    port_code           VARCHAR,
    loaded_imports_teu  DOUBLE,
    empty_imports_teu   DOUBLE,
    total_imports_teu   DOUBLE,
    loaded_exports_teu  DOUBLE,
    empty_exports_teu   DOUBLE,
    total_exports_teu   DOUBLE,
    total_teu           DOUBLE,
    prior_year_change_pct DOUBLE,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
);
""",
)

SUPPLY_CHAIN_INDEX = TableSchema(
    name="supply_chain_index",
    partition_cols=["partition_date", "source"],
    dedup_keys=["index_date", "source"],
    version="0.1.0",
    description="Global supply chain pressure index readings",
    raw_sql="""
CREATE TABLE IF NOT EXISTS supply_chain_index (
    index_date          DATE,
    gscpi_index         DOUBLE,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
);
""",
)

MARITIME_FREIGHT = TableSchema(
    name="maritime_freight",
    partition_cols=["partition_date", "source"],
    dedup_keys=["time_period", "geo", "direction", "unit", "source"],
    version="0.1.0",
    description="EU maritime freight throughput by port and direction",
    raw_sql="""
CREATE TABLE IF NOT EXISTS maritime_freight (
    time_period         VARCHAR,
    geo                 VARCHAR,
    geo_label           VARCHAR,
    direction           VARCHAR,
    direction_label     VARCHAR,
    unit                VARCHAR,
    unit_label          VARCHAR,
    value               DOUBLE,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
);
""",
)

SANCTIONS = TableSchema(
    name="sanctions",
    partition_cols=["partition_date", "source"],
    dedup_keys=["entity_id", "name", "source"],
    version="0.1.0",
    description="OFAC Specially Designated Nationals (SDN) list entries",
    raw_sql="""
CREATE TABLE IF NOT EXISTS sanctions (
    entity_id           VARCHAR,
    name                VARCHAR,
    aliases             VARCHAR,
    entity_type         VARCHAR,
    programs            VARCHAR,
    country             VARCHAR,
    title               VARCHAR,
    vessel_flag         VARCHAR,
    vessel_type         VARCHAR,
    vessel_tonnage      VARCHAR,
    gross_registered_tonnage VARCHAR,
    call_sign           VARCHAR,
    imo_number          VARCHAR,
    remarks             VARCHAR,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
);
""",
)

STORM_EVENTS = TableSchema(
    name="storm_events",
    partition_cols=["partition_date", "source"],
    dedup_keys=["event_id", "source"],
    version="0.1.0",
    description="NOAA Storm Events Database details (severe weather with damage)",
    raw_sql="""
CREATE TABLE IF NOT EXISTS storm_events (
    event_id            VARCHAR,
    event_type          VARCHAR,
    begin_date          TIMESTAMP,
    end_date            TIMESTAMP,
    state               VARCHAR,
    county              VARCHAR,
    latitude            DOUBLE,
    longitude           DOUBLE,
    injuries_direct     INTEGER,
    injuries_indirect   INTEGER,
    deaths_direct       INTEGER,
    deaths_indirect     INTEGER,
    damage_property_millions DOUBLE,
    damage_crops_millions DOUBLE,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
);
""",
)

PORT_METRICS = TableSchema(
    name="port_metrics",
    partition_cols=["partition_date", "source"],
    dedup_keys=["metric_period", "metric_name", "category", "source"],
    version="0.1.0",
    description="Port-level throughput metrics (Singapore MPA)",
    raw_sql="""
CREATE TABLE IF NOT EXISTS port_metrics (
    metric_period       VARCHAR,
    metric_year         INTEGER,
    metric_name         VARCHAR,
    category            VARCHAR,
    value               DOUBLE,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
);
""",
)

PORT_CONGESTION = TableSchema(
    name="port_congestion",
    partition_cols=["partition_date", "source"],
    dedup_keys=["snapshot_date", "port_code", "source"],
    version="0.1.0",
    description="Port congestion metrics (FreightPulse; retired 2026-09-24, history only)",
    raw_sql="""
CREATE TABLE IF NOT EXISTS port_congestion (
    snapshot_date           DATE,
    port_code               VARCHAR,
    port_name               VARCHAR,
    country                 VARCHAR,
    region                  VARCHAR,
    latitude                DOUBLE,
    longitude               DOUBLE,
    capacity_teu            DOUBLE,
    congestion_index        DOUBLE,
    congestion_level        VARCHAR,
    vessels_at_anchor       INTEGER,
    vessels_at_berth        INTEGER,
    avg_wait_time_hours     DOUBLE,
    avg_berth_time_hours    DOUBLE,
    container_dwell_days    DOUBLE,
    trend                   VARCHAR,
    change_week             INTEGER,
    source                  VARCHAR,
    partition_date          DATE,
    ingested_at             TIMESTAMP DEFAULT now()
);
""",
)

FUEL_PRICES = TableSchema(
    name="fuel_prices",
    partition_cols=["partition_date", "source"],
    dedup_keys=["snapshot_date", "source"],
    version="0.1.0",
    description="Road diesel, retail gasoline, and marine bunker fuel prices (FreightPulse)",
    raw_sql="""
CREATE TABLE IF NOT EXISTS fuel_prices (
    snapshot_date           DATE,
    diesel_national_avg     DOUBLE,
    diesel_change_week      DOUBLE,
    diesel_east_coast       DOUBLE,
    diesel_midwest          DOUBLE,
    diesel_gulf_coast       DOUBLE,
    diesel_rocky_mountain   DOUBLE,
    diesel_west_coast       DOUBLE,
    diesel_california       DOUBLE,
    gasoline_regular        DOUBLE,
    gasoline_midgrade       DOUBLE,
    gasoline_premium        DOUBLE,
    gasoline_national_avg   DOUBLE,
    bunker_rotterdam        DOUBLE,
    bunker_singapore        DOUBLE,
    bunker_houston          DOUBLE,
    diesel_30d_avg          DOUBLE,
    diesel_90d_avg          DOUBLE,
    diesel_yoy_change       DOUBLE,
    source                  VARCHAR,
    partition_date          DATE,
    ingested_at             TIMESTAMP DEFAULT now()
);
""",
)

SUPPLY_CHAIN_DISRUPTIONS = TableSchema(
    name="supply_chain_disruptions",
    partition_cols=["partition_date", "source"],
    dedup_keys=["snapshot_date", "disruption_id", "source"],
    version="0.1.0",
    description="Active supply chain disruption alerts (FreightPulse)",
    raw_sql="""
CREATE TABLE IF NOT EXISTS supply_chain_disruptions (
    snapshot_date           DATE,
    disruption_id           VARCHAR,
    disruption_type         VARCHAR,
    severity                VARCHAR,
    title                   VARCHAR,
    description             VARCHAR,
    affected_regions        VARCHAR,
    affected_routes         VARCHAR,
    transit_delay_days      INTEGER,
    rate_increase_pct       DOUBLE,
    capacity_reduction_pct  DOUBLE,
    started_at              VARCHAR,
    expected_resolution     VARCHAR,
    status                  VARCHAR,
    source                  VARCHAR,
    partition_date          DATE,
    ingested_at             TIMESTAMP DEFAULT now()
);
""",
)

AIR_CARGO = TableSchema(
    name="air_cargo",
    partition_cols=["partition_date", "source"],
    dedup_keys=["cargo_year", "cargo_month", "carrier_code", "origin", "dest", "source"],
    version="0.1.0",
    description="BTS T-100 air cargo freight tonnage by carrier and route",
    raw_sql="""
CREATE TABLE IF NOT EXISTS air_cargo (
    cargo_year          INTEGER,
    cargo_month         INTEGER,
    carrier_code        VARCHAR,
    carrier_name        VARCHAR,
    origin              VARCHAR,
    dest                VARCHAR,
    freight_pounds      DOUBLE,
    passengers          DOUBLE,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
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

CARRIERS = TableSchema(
    name="carriers",
    partition_cols=["snapshot_date"],
    dedup_keys=["snapshot_date", "carrier_code"],
    description=(
        "Ocean, trucking, and air carrier performance data"
        " (FreightPulse; retired 2026-09-24, history only)"
    ),
    raw_sql="""
CREATE TABLE IF NOT EXISTS carriers (
    snapshot_date           DATE NOT NULL,
    carrier_name            VARCHAR,
    carrier_code            VARCHAR NOT NULL,
    carrier_type            VARCHAR NOT NULL,
    country                 VARCHAR,
    fleet_size              BIGINT,
    fleet_size_unit         VARCHAR,
    vehicle_count           BIGINT,
    reliability_score       DOUBLE,
    market_share_pct        DOUBLE,
    on_time_performance_pct DOUBLE,
    avg_delay_hours         DOUBLE,
    customer_rating         DOUBLE,
    source                  VARCHAR NOT NULL DEFAULT 'freightpulse_carriers',
    ingested_at             TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
""",
)

PORT_ACTIVITY = TableSchema(
    name="port_activity",
    # Partitioned by year, not day: ~2,000 ports x ~2,800 days would otherwise
    # be thousands of tiny parquet directories.
    partition_cols=["year", "source"],
    dedup_keys=["activity_date", "port_id", "source"],
    description=(
        "Daily port calls and estimated import/export volume (metric tonnes) by "
        "vessel type for ~2,000 ports worldwide, from AIS (IMF PortWatch)"
    ),
    raw_sql="""
CREATE TABLE IF NOT EXISTS port_activity (
    activity_date           DATE,
    year                    INTEGER,
    port_id                 VARCHAR,
    port_name               VARCHAR,
    country                 VARCHAR,
    iso3                    VARCHAR,
    portcalls_container     INTEGER,
    portcalls_dry_bulk      INTEGER,
    portcalls_general_cargo INTEGER,
    portcalls_roro          INTEGER,
    portcalls_tanker        INTEGER,
    portcalls_cargo         INTEGER,
    portcalls               INTEGER,
    import_container        DOUBLE,
    import_dry_bulk         DOUBLE,
    import_general_cargo    DOUBLE,
    import_roro             DOUBLE,
    import_tanker           DOUBLE,
    import_cargo            DOUBLE,
    import_total            DOUBLE,
    export_container        DOUBLE,
    export_dry_bulk         DOUBLE,
    export_general_cargo    DOUBLE,
    export_roro             DOUBLE,
    export_tanker           DOUBLE,
    export_cargo            DOUBLE,
    export_total            DOUBLE,
    source                  VARCHAR,
    partition_date          DATE,
    ingested_at             TIMESTAMP DEFAULT now()
);
""",
)

VESSEL_TRACKS_US = TableSchema(
    name="vessel_tracks_us",
    # One partition per monthly NOAA file.
    partition_cols=["track_month", "source"],
    # Unique across all 1,273,672 tracks of the 2025-12 file, no null mmsi.
    dedup_keys=["mmsi", "start_time", "source"],
    description=(
        "Vessel tracks in US waters from NOAA MarineCadastre AIS, one row per "
        "track (a vessel's continuous movement, split at day boundaries), all "
        "vessel types, without the line geometry. Published ~145-165 days after "
        "collection, added about quarterly"
    ),
    raw_sql="""
CREATE TABLE IF NOT EXISTS vessel_tracks_us (
    track_month       DATE,
    mmsi              INTEGER,
    imo               VARCHAR,
    vessel_name       VARCHAR,
    call_sign         VARCHAR,
    vessel_type       INTEGER,
    vessel_type_name  VARCHAR,
    status            INTEGER,
    length            DOUBLE,
    width             INTEGER,
    draft             DOUBLE,
    cargo             INTEGER,
    transceiver       VARCHAR,
    duration_minutes  INTEGER,
    start_time        TIMESTAMP,
    end_time          TIMESTAMP,
    source            VARCHAR,
    partition_date    DATE,
    ingested_at       TIMESTAMP DEFAULT now()
);
""",
)

PORT_PROFILES = TableSchema(
    name="port_profiles",
    partition_cols=[],
    description=(
        "Reference data for the IMF PortWatch ports: location, UN/LOCODE, vessel "
        "mix, top industries, and share of national maritime trade"
    ),
    raw_sql="""
CREATE TABLE IF NOT EXISTS port_profiles (
    port_id                        VARCHAR PRIMARY KEY,
    port_name                      VARCHAR,
    full_name                      VARCHAR,
    country                        VARCHAR,
    iso3                           VARCHAR,
    continent                      VARCHAR,
    locode                         VARCHAR,
    latitude                       DOUBLE,
    longitude                      DOUBLE,
    vessel_count_total             INTEGER,
    vessel_count_container         INTEGER,
    vessel_count_dry_bulk          INTEGER,
    vessel_count_general_cargo     INTEGER,
    vessel_count_roro              INTEGER,
    vessel_count_tanker            INTEGER,
    industry_top1                  VARCHAR,
    industry_top2                  VARCHAR,
    industry_top3                  VARCHAR,
    share_country_maritime_import  DOUBLE,
    share_country_maritime_export  DOUBLE,
    source                         VARCHAR,
    ingested_at                    TIMESTAMP DEFAULT now()
);
""",
)

TRADE_NOWCAST = TableSchema(
    name="trade_nowcast",
    partition_cols=["source"],
    dedup_keys=["month_date", "region", "source"],
    description=(
        "Monthly AIS-based port calls and trade value/volume nowcasts by country "
        "and region group (IMF PortWatch TradeNow)"
    ),
    raw_sql="""
CREATE TABLE IF NOT EXISTS trade_nowcast (
    month_date                    DATE,
    region                        VARCHAR,
    iso3                          VARCHAR,
    ais_portcalls_container       DOUBLE,
    ais_portcalls_general_cargo   DOUBLE,
    ais_portcalls_dry_bulk        DOUBLE,
    ais_portcalls_roro            DOUBLE,
    ais_portcalls_tanker          DOUBLE,
    ais_import_tanker             DOUBLE,
    ais_export_tanker             DOUBLE,
    ais_import_dry_bulk           DOUBLE,
    ais_export_dry_bulk           DOUBLE,
    ais_import_container          DOUBLE,
    ais_export_container          DOUBLE,
    ais_import_general_cargo      DOUBLE,
    ais_export_general_cargo      DOUBLE,
    ais_import_roro               DOUBLE,
    ais_export_roro               DOUBLE,
    value_import_total            DOUBLE,
    value_export_total            DOUBLE,
    volume_import_total           DOUBLE,
    volume_export_total           DOUBLE,
    trade_value                   DOUBLE,
    trade_volume                  DOUBLE,
    source                        VARCHAR,
    partition_date                DATE,
    ingested_at                   TIMESTAMP DEFAULT now()
);
""",
)

DISRUPTION_EVENTS = TableSchema(
    name="disruption_events",
    partition_cols=["source"],
    dedup_keys=["event_id", "episode_id", "source"],
    description=(
        "Natural-hazard disruption events (GDACS alerts) with affected ports and "
        "countries, as curated by IMF PortWatch"
    ),
    raw_sql="""
CREATE TABLE IF NOT EXISTS disruption_events (
    event_id            VARCHAR,
    episode_id          VARCHAR,
    event_type          VARCHAR,
    event_name          VARCHAR,
    description         VARCHAR,
    alert_level         VARCHAR,
    alert_score         DOUBLE,
    severity_text       VARCHAR,
    country             VARCHAR,
    iso3                VARCHAR,
    affected_countries  VARCHAR,
    affected_ports      VARCHAR,
    n_affected_ports    INTEGER,
    affected_population VARCHAR,
    is_current          BOOLEAN,
    from_date           TIMESTAMP,
    to_date             TIMESTAMP,
    latitude            DOUBLE,
    longitude           DOUBLE,
    source              VARCHAR,
    partition_date      DATE,
    ingested_at         TIMESTAMP DEFAULT now()
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
    PORT_VOLUMES,
    SUPPLY_CHAIN_INDEX,
    MARITIME_FREIGHT,
    SANCTIONS,
    STORM_EVENTS,
    PORT_METRICS,
    FUEL_PRICES,
    SUPPLY_CHAIN_DISRUPTIONS,
    AIR_CARGO,
    PORT_CONGESTION,
    CARRIERS,
    PORT_ACTIVITY,
    PORT_PROFILES,
    VESSEL_TRACKS_US,
    TRADE_NOWCAST,
    DISRUPTION_EVENTS,
    SOURCE_TRACKING,
    SCHEMA_MIGRATIONS,
    LINEAGE_EVENTS,
]
