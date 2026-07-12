import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pandas as pd
from pathlib import Path
import json
from datetime import datetime

from config import BASE_DIR, PARQUET_DIR


class Storage:
    def __init__(self, db_path=None):
        if db_path is None:
            db_path = str(BASE_DIR / "shipping.duckdb")
        self.conn = duckdb.connect(db_path)
        self._init_tables()

    def _init_tables(self):
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS bills_of_lading (
                id VARCHAR PRIMARY KEY,
                shipper_name VARCHAR,
                shipper_address VARCHAR,
                consignee_name VARCHAR,
                consignee_address VARCHAR,
                notify_party VARCHAR,
                carrier VARCHAR,
                vessel_name VARCHAR,
                voyage_number VARCHAR,
                port_of_loading VARCHAR,
                port_of_discharge VARCHAR,
                place_of_receipt VARCHAR,
                place_of_delivery VARCHAR,
                vessel_country VARCHAR,
                vessel_type VARCHAR,
                vessel_gross_tonnage DOUBLE,
                vessel_year_built INTEGER,
                vessel_imo VARCHAR,
                container_number VARCHAR,
                container_size VARCHAR,
                container_type VARCHAR,
                container_teu DOUBLE,
                seal_number VARCHAR,
                commodity_description VARCHAR,
                hs_code VARCHAR,
                weight_kg DOUBLE,
                weight_lbs DOUBLE,
                volume_cbm DOUBLE,
                packages INTEGER,
                package_type VARCHAR,
                freight_charges DOUBLE,
                freight_currency VARCHAR,
                insurance_charges DOUBLE,
                insurance_currency VARCHAR,
                total_charges DOUBLE,
                total_currency VARCHAR,
                etd DATE,
                eta DATE,
                departure_date DATE,
                arrival_date DATE,
                customs_status VARCHAR,
                bill_of_lading_number VARCHAR,
                booking_number VARCHAR,
                source VARCHAR,
                source_country VARCHAR,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_flows (
                id VARCHAR PRIMARY KEY,
                period VARCHAR,
                reporter_code INTEGER,
                reporter_name VARCHAR,
                flow_code INTEGER,
                flow_desc VARCHAR,
                partner_code INTEGER,
                partner_name VARCHAR,
                commodity_code VARCHAR,
                commodity_desc VARCHAR,
                primary_value DOUBLE,
                net_weight_kg DOUBLE,
                gross_weight_kg DOUBLE,
                trade_type VARCHAR,
                source VARCHAR,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS vessel_presence (
                id VARCHAR PRIMARY KEY,
                vessel_id VARCHAR,
                vessel_name VARCHAR,
                vessel_type VARCHAR,
                imo_number VARCHAR,
                mmsi VARCHAR,
                lat DOUBLE,
                lon DOUBLE,
                speed_knots DOUBLE,
                course DOUBLE,
                heading DOUBLE,
                distance_from_shore_km DOUBLE,
                distance_from_port_km DOUBLE,
                port_id VARCHAR,
                port_name VARCHAR,
                start_time TIMESTAMP,
                end_time TIMESTAMP,
                hours_present DOUBLE,
                source VARCHAR,
                source_country VARCHAR,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS freight_rates (
                id VARCHAR PRIMARY KEY,
                date DATE,
                index_name VARCHAR,
                index_value DOUBLE,
                route VARCHAR,
                vessel_class VARCHAR,
                period_desc VARCHAR,
                source VARCHAR,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS tariff_rates (
                id VARCHAR PRIMARY KEY,
                reporter_code INTEGER,
                reporter_name VARCHAR,
                product_code VARCHAR,
                product_desc VARCHAR,
                partner_code INTEGER,
                partner_name VARCHAR,
                ad_valorem_rate DOUBLE,
                tariff_type VARCHAR,
                period VARCHAR,
                source VARCHAR,
                ingested_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS hs_reference (
                hs_code VARCHAR PRIMARY KEY,
                hs_level INTEGER,
                description VARCHAR,
                parent_code VARCHAR,
                chapter VARCHAR,
                section VARCHAR
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS port_reference (
                port_id VARCHAR PRIMARY KEY,
                port_name VARCHAR,
                country_code VARCHAR,
                country_name VARCHAR,
                un_locode VARCHAR,
                latitude DOUBLE,
                longitude DOUBLE,
                region VARCHAR
            )
        """)

    def insert_dataframe(self, table_name: str, df: pd.DataFrame):
        self.conn.execute(f"INSERT OR IGNORE INTO {table_name} SELECT * FROM df")

    def query(self, sql: str) -> pd.DataFrame:
        return self.conn.execute(sql).fetchdf()

    def list_tables(self) -> list:
        return self.conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'main'"
        ).fetchdf()["table_name"].tolist()

    def table_count(self, table_name: str) -> int:
        return self.conn.execute(f"SELECT COUNT(*) as cnt FROM {table_name}").fetchone()[0]

    def close(self):
        self.conn.close()
