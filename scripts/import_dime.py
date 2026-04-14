#!/usr/bin/env python3
"""
Import DIME (Database on Ideology, Money in Politics, and Elections) data
into PostgreSQL.

Downloads data from Harvard Dataverse (public, no auth required) and loads
into dime_* tables.  DIME provides CFscore ideology estimates for candidates
and contributors — the main value-add over raw FEC data.

Usage:
    # Download and load everything (recipients + all contribution cycles + donors)
    python scripts/import_dime.py

    # Download only (no database required)
    python scripts/import_dime.py --download-only

    # Load from already-downloaded files
    python scripts/import_dime.py --load-only --data-dir .tmp/dime_data

    # Only specific datasets
    python scripts/import_dime.py --datasets recipients contributions

    # Only specific contribution cycles
    python scripts/import_dime.py --datasets contributions --cycles 2012 2014

    # Reset tables before loading
    python scripts/import_dime.py --reset

    # Specify database URL
    DATABASE_URL=postgresql://user:pass@host/db python scripts/import_dime.py
"""

import argparse
import logging
import os
import sys
import time
from pathlib import Path

# Add the repo root to sys.path so we can import backend modules
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.datatalk.pipeline.scrapers.dime import (
    ALL_DATASETS,
    DEFAULT_DATA_DIR,
    get_contrib_cycles,
    download_all,
)
from backend.datatalk.pipeline.loaders.dime_loader import load_all

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download and load DIME data into PostgreSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--download-only",
        action="store_true",
        help="Download data files but do not load into database",
    )
    parser.add_argument(
        "--load-only",
        action="store_true",
        help="Load from already-downloaded files (skip download)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if files already exist",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=DEFAULT_DATA_DIR,
        help=f"Directory for downloaded data files (default: {DEFAULT_DATA_DIR})",
    )
    parser.add_argument(
        "--datasets",
        nargs="+",
        choices=["recipients", "contributions", "donors"],
        help="Only process specific datasets (default: all)",
    )
    parser.add_argument(
        "--cycles",
        nargs="+",
        type=int,
        help="Only download/load specific contribution cycles (e.g. 2012 2014)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all DIME tables before loading (WARNING: deletes data)",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="PostgreSQL connection URL (default: $DATABASE_URL)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    return parser.parse_args()


def format_duration(seconds: float) -> str:
    """Format a duration in human-readable form."""
    if seconds < 60:
        return f"{seconds:.1f}s"
    minutes = int(seconds // 60)
    secs = seconds % 60
    if minutes < 60:
        return f"{minutes}m {secs:.0f}s"
    hours = int(minutes // 60)
    mins = minutes % 60
    return f"{hours}h {mins}m"


def progress_callback(dataset_name: str, rows: int):
    """Print progress during data loading."""
    if rows % 100000 == 0:
        print(f"  {dataset_name}: {rows:,} rows loaded", flush=True)


def main():
    args = parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.download_only and args.load_only:
        print("Error: --download-only and --load-only are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    dataset_names = args.datasets
    available_cycles = get_contrib_cycles()

    if args.cycles:
        invalid = [c for c in args.cycles if c not in available_cycles]
        if invalid:
            print(f"Error: invalid cycles: {invalid}. Available: {available_cycles}", file=sys.stderr)
            sys.exit(1)

    print("DIME Data Import")
    print(f"Datasets: {', '.join(dataset_names) if dataset_names else 'all'}")
    if args.cycles:
        print(f"Contribution cycles: {args.cycles}")
    print(f"Data directory: {args.data_dir}")
    print()

    start = time.time()

    # --- Download phase ---
    if not args.load_only:
        print("=== Downloading from Harvard Dataverse ===")
        dl_start = time.time()

        downloaded = download_all(
            data_dir=args.data_dir,
            cycles=args.cycles,
            datasets=dataset_names,
            force=args.force,
        )

        dl_duration = time.time() - dl_start
        total_files = sum(len(v) for v in downloaded.values())
        print(f"\nDownloaded {total_files} files in {format_duration(dl_duration)}")
        for ds_name, paths in downloaded.items():
            for p in paths:
                size_mb = p.stat().st_size / (1024 * 1024)
                print(f"  {ds_name}: {p.name} ({size_mb:.1f} MB)")
        print()

    # --- Load phase ---
    if not args.download_only:
        database_url = args.database_url or os.environ.get("DATABASE_URL")
        if database_url:
            display_url = database_url
            if "@" in display_url:
                pre_at = display_url.split("@")[0]
                if ":" in pre_at:
                    parts = pre_at.split(":")
                    display_url = ":".join(parts[:-1]) + ":****@" + database_url.split("@", 1)[1]
        else:
            display_url = "(default: datatalk@localhost/datatalk)"

        print("=== Loading into PostgreSQL ===")
        print(f"Database: {display_url}")

        if args.reset:
            print("Resetting: dropping all DIME tables...")

        load_start = time.time()

        try:
            results = load_all(
                data_dir=args.data_dir,
                database_url=database_url,
                datasets=dataset_names,
                reset=args.reset,
                progress_callback=progress_callback,
            )
        except Exception as e:
            print(f"\nError loading data: {e}", file=sys.stderr)
            logger.exception("Load failed")
            sys.exit(1)

        load_duration = time.time() - load_start
        print(f"\nLoaded data in {format_duration(load_duration)}")
        for table_name, row_count in results.items():
            print(f"  {table_name}: {row_count:,} rows")
        print()

    total_duration = time.time() - start
    print(f"Done in {format_duration(total_duration)}")


if __name__ == "__main__":
    main()
