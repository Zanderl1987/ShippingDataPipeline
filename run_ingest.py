import os
import sys
import argparse
from datetime import datetime

from config import BASE_DIR
from storage.duckdb_storage import Storage
from ingestion.importyeti import ImportYetiIngestor
from ingestion.comtrade import ComtradeIngestor
from ingestion.global_fishing_watch import GFWIngestor
from ingestion.us_itc import USITCIngestor
from ingestion.marine_cadastre import MarineCadastreIngestor
from ingestion.freight_rates import BalticExchangeIngestor
from ingestion.wto import WTOIngestor


def main():
    parser = argparse.ArgumentParser(description="Shipping & Trade Data Ingestion Pipeline")
    parser.add_argument("--source", type=str, help="Data source to ingest")
    parser.add_argument("--all", action="store_true", help="Ingest from all available sources")
    parser.add_argument("--list-sources", action="store_true", help="List all available sources")
    parser.add_argument("--query", type=str, help="Search query or parameter")
    parser.add_argument("--country", type=str, help="Country code for targeted queries")
    parser.add_argument("--start-date", type=str, help="Start date (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, help="End date (YYYY-MM-DD)")

    args = parser.parse_args()

    if args.list_sources:
        print("Available data sources:")
        print("  importyeti      - US Bill of Lading data (ImportYeti API)")
        print("  comtrade        - Global trade flows (UN Comtrade)")
        print("  gfw             - Vessel presence/traffic (Global Fishing Watch)")
        print("  usitc           - HTS code reference (US ITC)")
        print("  marine_cadastre - US vessel traffic (Marine Cadastre)")
        print("  freight         - Freight rate indices (Baltic Exchange)")
        print("  wto             - Tariff and trade data (WTO)")
        print("  all             - All sources")
        return

    storage = Storage()

    ingestors = {
        "importyeti": lambda: ImportYetiIngestor(storage),
        "comtrade": lambda: ComtradeIngestor(storage),
        "gfw": lambda: GFWIngestor(storage),
        "usitc": lambda: USITCIngestor(storage),
        "marine_cadastre": lambda: MarineCadastreIngestor(storage),
        "freight": lambda: BalticExchangeIngestor(storage),
        "wto": lambda: WTOIngestor(storage),
    }

    if args.all:
        print(f"=== Full ingestion started at {datetime.utcnow().isoformat()} ===")
        for name, factory in ingestors.items():
            try:
                print(f"\n--- {name} ---")
                ingestor = factory()
                if name == "usitc":
                    df = ingestor.ingest_all_chapters()
                    print(f"  Ingested {len(df)} HS codes")
                elif name == "freight":
                    df = ingestor.ingest_all_indices()
                    print(f"  Ingested {len(df)} freight records")
                elif name == "wto":
                    df = ingestor.ingest_top_traders()
                    print(f"  Ingested {len(df)} WTO trade records")
                else:
                    print(f"  {name}: ready (requires --query parameter)")
            except Exception as e:
                print(f"  Error with {name}: {e}")
        print(f"\n=== Ingestion completed at {datetime.utcnow().isoformat()} ===")
        _print_summary(storage)
        return

    if not args.source:
        parser.print_help()
        return

    source = args.source.lower()
    if source not in ingestors:
        print(f"Unknown source: {source}")
        print(f"Available: {', '.join(ingestors.keys())}")
        return

    ingestor = ingestors[source]()

    if source == "importyeti":
        if args.query:
            df = ingestor.search_company(args.query)
            print(f"Search results: {df}")
        else:
            print("ImportYeti: use --query 'company name' to search")

    elif source == "comtrade":
        if args.country:
            df = ingestor.get_top_traders("2023")
            print(f"Top traders: {len(df)} rows")
        else:
            print("Comtrade: use --country for bilateral queries")

    elif source == "gfw":
        if args.query:
            lat, lon = map(float, args.query.split(","))
            df = ingestor.get_vessel_presence(lat, lon)
            print(f"Vessel presence: {len(df)} positions")
        else:
            print("GFW: use --query 'lat,lon' for vessel presence")

    elif source == "usitc":
        if args.query:
            df = ingestor.search_hts(args.query)
            print(f"HTS search results: {len(df)} rows")
        else:
            df = ingestor.ingest_all_chapters()
            print(f"Ingested {len(df)} HS codes")

    elif source == "marine_cadastre":
        if args.query:
            lat, lon = map(float, args.query.split(","))
            df = ingestor.ingest_vessel_activity(lat, lon)
            print(f"Vessel activity: {len(df)} records")
        else:
            print("Marine Cadastre: use --query 'lat,lon' for vessel activity")

    elif source == "freight":
        df = ingestor.ingest_all_indices()
        print(f"Ingested {len(df)} freight records")

    elif source == "wto":
        df = ingestor.ingest_top_traders()
        print(f"Ingested {len(df)} WTO records")

    _print_summary(storage)


def _print_summary(storage: Storage):
    print("\n=== Storage Summary ===")
    for table in storage.list_tables():
        count = storage.table_count(table)
        print(f"  {table}: {count} rows")
    print()


if __name__ == "__main__":
    main()
