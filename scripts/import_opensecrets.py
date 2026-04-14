#!/usr/bin/env python3
"""
Import OpenSecrets campaign finance data into PostgreSQL.

Usage:
    # Load from local CSV files (development / sample data):
    python scripts/import_opensecrets.py --data-dir backend/datatalk/pipeline/sample_data/opensecrets/

    # Download and load (when API access is available):
    python scripts/import_opensecrets.py --download --cycle 2024

    # Specify database URL:
    DATABASE_URL=postgresql://user:pass@localhost/datatalk python scripts/import_opensecrets.py --data-dir ./data/

Environment variables:
    DATABASE_URL            PostgreSQL connection string (default: postgresql://localhost/datatalk)
    OPENSECRETS_EMAIL       Account email for bulk downloads
    OPENSECRETS_PASSWORD    Account password for bulk downloads
"""

import argparse
import logging
import os
import sys
import time

import psycopg2
from rich.console import Console
from rich.table import Table

# Allow running from repo root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.datatalk.pipeline.loaders.opensecrets_loader import (
    create_indexes,
    create_schema,
    full_import,
    load_from_csv,
)
from backend.datatalk.pipeline.scrapers.opensecrets import (
    DATASETS,
    DownloadConfig,
    download_bulk_data,
)

console = Console()


def get_connection(database_url: str):
    """Create a psycopg2 connection from a URL."""
    try:
        conn = psycopg2.connect(database_url)
        return conn
    except psycopg2.OperationalError as e:
        console.print(f"[red]Failed to connect to database:[/red] {e}")
        console.print(
            "\nSet DATABASE_URL or ensure PostgreSQL is running at the default location."
        )
        sys.exit(1)


def cmd_load(args):
    """Load OpenSecrets data from local CSV files."""
    database_url = args.database_url or os.environ.get(
        "DATABASE_URL", "postgresql://localhost/datatalk"
    )
    data_dir = args.data_dir

    console.print(f"[bold]Loading OpenSecrets data from:[/bold] {data_dir}")
    console.print(f"[bold]Database:[/bold] {database_url.split('@')[-1] if '@' in database_url else database_url}")
    console.print()

    conn = get_connection(database_url)
    try:
        t0 = time.time()
        results = full_import(
            conn,
            data_dir,
            cycle=args.cycle,
            batch_size=args.batch_size,
        )
        elapsed = time.time() - t0

        # Print results table
        table = Table(title="Import Results")
        table.add_column("Dataset", style="cyan")
        table.add_column("Rows Loaded", justify="right", style="green")

        total_rows = 0
        for name, count in results.items():
            table.add_row(name, str(count))
            total_rows += count

        # Note datasets with no CSV found
        loaded_names = set(results.keys())
        for ds in DATASETS:
            if ds.name not in loaded_names:
                table.add_row(ds.name, "[yellow]no CSV found[/yellow]")

        console.print(table)
        console.print(
            f"\n[bold green]Done.[/bold green] {total_rows} total rows in {elapsed:.1f}s"
        )

    finally:
        conn.close()


def cmd_download(args):
    """Download bulk data from OpenSecrets and load it."""
    email = args.email or os.environ.get("OPENSECRETS_EMAIL", "")
    password = args.password or os.environ.get("OPENSECRETS_PASSWORD", "")

    config = DownloadConfig(
        email=email,
        password=password,
        cycle=args.cycle,
        output_dir=args.output_dir,
    )

    console.print(f"[bold]Downloading OpenSecrets data for cycle {args.cycle}...[/bold]")

    try:
        files = download_bulk_data(config)
        console.print(f"Downloaded {len(files)} files to {args.output_dir}")

        # Now load the downloaded data
        args.data_dir = args.output_dir
        cmd_load(args)

    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        sys.exit(1)


def cmd_schema_only(args):
    """Create the OpenSecrets schema without loading data."""
    database_url = args.database_url or os.environ.get(
        "DATABASE_URL", "postgresql://localhost/datatalk"
    )
    conn = get_connection(database_url)
    try:
        create_schema(conn)
        create_indexes(conn)
        console.print("[bold green]Schema created successfully.[/bold green]")
        for ds in DATASETS:
            console.print(f"  - {ds.table_name}: {len(ds.columns)} columns")
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description="Import OpenSecrets campaign finance data into PostgreSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--database-url",
        default=None,
        help="PostgreSQL connection URL (default: DATABASE_URL env var or postgresql://localhost/datatalk)",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    subparsers = parser.add_subparsers(dest="command")

    # -- load subcommand (default) --
    load_parser = subparsers.add_parser("load", help="Load from local CSV files")
    load_parser.add_argument(
        "--data-dir",
        required=True,
        help="Directory containing OpenSecrets CSV files",
    )
    load_parser.add_argument(
        "--cycle",
        type=int,
        default=None,
        help="Election cycle (for idempotent delete-then-insert)",
    )
    load_parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
        help="Rows per INSERT batch (default: 1000)",
    )

    # -- download subcommand --
    dl_parser = subparsers.add_parser("download", help="Download from OpenSecrets")
    dl_parser.add_argument(
        "--cycle",
        type=int,
        default=2024,
        help="Election cycle to download (default: 2024)",
    )
    dl_parser.add_argument(
        "--email",
        default=None,
        help="OpenSecrets account email (default: OPENSECRETS_EMAIL env var)",
    )
    dl_parser.add_argument(
        "--password",
        default=None,
        help="OpenSecrets account password (default: OPENSECRETS_PASSWORD env var)",
    )
    dl_parser.add_argument(
        "--output-dir",
        default=".tmp/opensecrets_data",
        help="Directory to save downloaded files",
    )
    dl_parser.add_argument(
        "--batch-size",
        type=int,
        default=1000,
    )

    # -- schema-only subcommand --
    subparsers.add_parser("schema", help="Create schema without loading data")

    # Support the flat --data-dir / --download flags for convenience
    parser.add_argument(
        "--data-dir",
        default=None,
        help="(Shortcut) Load from this directory — equivalent to 'load --data-dir'",
    )
    parser.add_argument(
        "--download",
        action="store_true",
        help="(Shortcut) Download data — equivalent to 'download' subcommand",
    )
    parser.add_argument(
        "--cycle",
        type=int,
        default=None,
        help="(Shortcut) Election cycle",
    )

    args = parser.parse_args()

    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    # Dispatch
    if args.command == "load":
        cmd_load(args)
    elif args.command == "download":
        cmd_download(args)
    elif args.command == "schema":
        cmd_schema_only(args)
    elif args.data_dir:
        # Flat --data-dir shortcut
        args.batch_size = 1000
        cmd_load(args)
    elif args.download:
        # Flat --download shortcut
        args.email = None
        args.password = None
        args.output_dir = ".tmp/opensecrets_data"
        args.batch_size = 1000
        if args.cycle is None:
            args.cycle = 2024
        cmd_download(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
