#!/usr/bin/env python3
"""
Import FEC campaign finance bulk data into PostgreSQL.

Usage:
    # Download and load 2023-2024 cycle (default)
    python scripts/import_fec.py --cycle 2024

    # Download only (no database required)
    python scripts/import_fec.py --cycle 2024 --download-only

    # Load from already-downloaded files
    python scripts/import_fec.py --cycle 2024 --load-only

    # Force re-download even if files exist
    python scripts/import_fec.py --cycle 2024 --force

    # Specify database URL
    DATABASE_URL=postgresql://user:pass@host:5432/db python scripts/import_fec.py

    # Only specific datasets
    python scripts/import_fec.py --cycle 2024 --datasets cn cm ccl
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

from backend.datatalk.pipeline.scrapers.fec import (
    ALL_DATASETS,
    DEFAULT_DATA_DIR,
    download_all,
    get_dataset_by_filename,
)
from backend.datatalk.pipeline.loaders.fec_loader import load_all

logger = logging.getLogger(__name__)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Download and load FEC bulk data into PostgreSQL.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--cycle",
        type=int,
        default=2024,
        help="Election cycle year (default: 2024 for the 2023-2024 cycle)",
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
        metavar="NAME",
        help="Only process specific datasets (by filename: cn, cm, indiv, pas2, ccl)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Drop and recreate all FEC tables before loading (WARNING: deletes all existing data)",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=None,
        help="PostgreSQL connection URL (default: $DATABASE_URL or postgresql://creator_role:creator_role@localhost:5432/datatalk)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose (DEBUG) logging",
    )
    return parser.parse_args()


def resolve_datasets(names: list[str] | None) -> list:
    """Resolve dataset names to FECDataset objects."""
    if names is None:
        return ALL_DATASETS

    datasets = []
    for name in names:
        ds = get_dataset_by_filename(name)
        if ds is None:
            valid = ", ".join(d.filename for d in ALL_DATASETS)
            print(f"Error: unknown dataset '{name}'. Valid names: {valid}", file=sys.stderr)
            sys.exit(1)
        datasets.append(ds)
    return datasets


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

    # Set up logging
    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if args.download_only and args.load_only:
        print("Error: --download-only and --load-only are mutually exclusive", file=sys.stderr)
        sys.exit(1)

    datasets = resolve_datasets(args.datasets)
    dataset_names = ", ".join(d.name for d in datasets)

    print(f"FEC Data Import — Cycle {args.cycle}")
    print(f"Datasets: {dataset_names}")
    print(f"Data directory: {args.data_dir}")
    print()

    start = time.time()

    # --- Download phase ---
    if not args.load_only:
        print("=== Downloading ===")
        dl_start = time.time()

        downloaded = download_all(
            cycle=args.cycle,
            data_dir=args.data_dir,
            force=args.force,
            datasets=datasets,
        )

        dl_duration = time.time() - dl_start
        print(f"\nDownloaded {len(downloaded)} files in {format_duration(dl_duration)}")
        for fname, fpath in downloaded.items():
            size_mb = fpath.stat().st_size / (1024 * 1024)
            print(f"  {fname}: {fpath} ({size_mb:.1f} MB)")
        print()

    # --- Load phase ---
    if not args.download_only:
        database_url = args.database_url or os.environ.get("DATABASE_URL")
        if database_url:
            # Mask password in log output
            display_url = database_url
            if "@" in display_url:
                pre_at = display_url.split("@")[0]
                if ":" in pre_at:
                    parts = pre_at.split(":")
                    display_url = ":".join(parts[:-1]) + ":****@" + database_url.split("@", 1)[1]
        else:
            display_url = "(default: creator_role@localhost/datatalk)"

        print("=== Loading into PostgreSQL ===")
        print(f"Database: {display_url}")

        if args.reset:
            print("Resetting: dropping all FEC tables...")
            from backend.datatalk.pipeline.loaders.fec_loader import (
                generate_drop_table_sql,
                get_connection,
                ALL_DATASETS,
            )
            conn = get_connection(database_url)
            conn.autocommit = True
            cur = conn.cursor()
            for ds in ALL_DATASETS:
                cur.execute(generate_drop_table_sql(ds))
            cur.close()
            conn.close()
            print("Tables dropped.\n")

        load_start = time.time()

        try:
            results = load_all(
                cycle=args.cycle,
                data_dir=args.data_dir,
                database_url=database_url,
                datasets=datasets,
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
