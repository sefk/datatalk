"""
DIME data loader for PostgreSQL.

Creates the dime_* tables, loads CSV.GZ data with streaming batch inserts,
creates indexes, and handles idempotent loads.  Designed to coexist with
FEC and OpenSecrets tables in the same database.

DIME data is large (850M+ contribution records).  This loader streams
through gzipped CSVs in chunks to avoid loading everything into memory.
"""

import logging
import os
from pathlib import Path

import psycopg2
import psycopg2.extras

from backend.datatalk.pipeline.scrapers.dime import (
    ALL_DATASETS,
    CONTRIBUTIONS,
    DEFAULT_DATA_DIR,
    DONORS,
    DIMEDataset,
    RECIPIENTS,
    find_data_files,
    parse_gz_csv,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Index definitions
# ---------------------------------------------------------------------------

INDEXES = [
    # Recipients
    ("idx_dime_recip_rid", "dime_recipients", "bonica_rid"),
    ("idx_dime_recip_fecyear", "dime_recipients", "fecyear"),
    ("idx_dime_recip_state", "dime_recipients", "state"),
    ("idx_dime_recip_party", "dime_recipients", "party"),
    ("idx_dime_recip_seat", "dime_recipients", "seat"),
    ("idx_dime_recip_cfscore", "dime_recipients", "recipient_cfscore"),
    ("idx_dime_recip_fecid", "dime_recipients", "fec_id"),
    ("idx_dime_recip_name", "dime_recipients", "name"),
    # Contributions
    ("idx_dime_contrib_cycle", "dime_contributions", "cycle"),
    ("idx_dime_contrib_rid", "dime_contributions", "bonica_rid"),
    ("idx_dime_contrib_cid", "dime_contributions", "bonica_cid"),
    ("idx_dime_contrib_state", "dime_contributions", "contributor_state"),
    ("idx_dime_contrib_date", "dime_contributions", "date"),
    ("idx_dime_contrib_amount", "dime_contributions", "amount"),
    ("idx_dime_contrib_seat", "dime_contributions", "seat"),
    ("idx_dime_contrib_rparty", "dime_contributions", "recipient_party"),
    # Donors
    ("idx_dime_donor_cid", "dime_donors", "bonica_cid"),
    ("idx_dime_donor_cfscore", "dime_donors", "contributor_cfscore"),
    ("idx_dime_donor_state", "dime_donors", "most_recent_contributor_state"),
]


# ---------------------------------------------------------------------------
# Schema helpers
# ---------------------------------------------------------------------------

def _normalize_col_name(name: str) -> str:
    """Convert DIME dot-notation to Postgres-safe underscores."""
    return name.strip().replace(".", "_").replace(" ", "_").lower()


def _column_def(col_name: str, col_type: str) -> str:
    """Build a column definition with normalized name."""
    return f"    {_normalize_col_name(col_name)} {col_type}"


def create_table_ddl(dataset: DIMEDataset) -> str:
    """Generate CREATE TABLE IF NOT EXISTS DDL for a dataset."""
    cols = ",\n".join(
        _column_def(name, pg_type) for name, pg_type in dataset.columns
    )
    return f"CREATE TABLE IF NOT EXISTS {dataset.table_name} (\n{cols}\n);"


def create_schema(conn) -> None:
    """Create all DIME tables.  Safe to call repeatedly."""
    with conn.cursor() as cur:
        for ds in ALL_DATASETS:
            ddl = create_table_ddl(ds)
            logger.debug("Executing DDL:\n%s", ddl)
            cur.execute(ddl)
    conn.commit()
    logger.info("DIME schema created/verified (%d tables)", len(ALL_DATASETS))


def create_indexes(conn) -> None:
    """Create indexes on key columns.  Skips existing indexes."""
    with conn.cursor() as cur:
        for idx_name, table_name, col_expr in INDEXES:
            ddl = (
                f"CREATE INDEX IF NOT EXISTS {idx_name} "
                f"ON {table_name} ({col_expr});"
            )
            logger.debug("Creating index: %s", idx_name)
            cur.execute(ddl)
    conn.commit()
    logger.info("Created/verified %d indexes", len(INDEXES))


def drop_tables(conn) -> None:
    """Drop all DIME tables.  Use with care."""
    with conn.cursor() as cur:
        for ds in ALL_DATASETS:
            cur.execute(f"DROP TABLE IF EXISTS {ds.table_name} CASCADE;")
    conn.commit()
    logger.info("Dropped all DIME tables")


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------

def _coerce_value(value: str | None, pg_type: str):
    """Coerce a string value to the appropriate Python type for Postgres."""
    if value is None or value == "" or value == "NA":
        return None

    pg_upper = pg_type.upper()
    if pg_upper == "SMALLINT" or pg_upper == "INTEGER":
        try:
            return int(float(value))  # handle "1.0" -> 1
        except (ValueError, OverflowError):
            return None
    elif pg_upper == "REAL":
        try:
            return float(value)
        except ValueError:
            return None
    elif pg_upper.startswith("NUMERIC"):
        try:
            return float(value)
        except ValueError:
            return None
    elif pg_upper == "DATE":
        # DIME dates are typically MM/DD/YYYY or YYYY-MM-DD
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
            try:
                from datetime import datetime
                return datetime.strptime(value, fmt).date()
            except ValueError:
                continue
        return None
    else:
        # VARCHAR — truncate to declared length if specified
        return value


# ---------------------------------------------------------------------------
# Data loading (streaming)
# ---------------------------------------------------------------------------

def _prepare_row(row: dict, dataset: DIMEDataset) -> tuple:
    """Convert a dict row to a tuple of Postgres-ready values."""
    values = []
    for col_name, pg_type in dataset.columns:
        norm = _normalize_col_name(col_name)
        raw = row.get(norm)
        values.append(_coerce_value(raw, pg_type))
    return tuple(values)


def load_file(
    conn,
    filepath: Path,
    dataset: DIMEDataset,
    *,
    chunk_size: int = 10000,
    progress_callback=None,
) -> int:
    """Load a single CSV.GZ file into its dataset table.

    Streams through the file in chunks using execute_values for fast inserts.
    Returns the number of rows inserted.
    """
    col_names = [_normalize_col_name(c[0]) for c in dataset.columns]
    placeholders = ", ".join(["%s"] * len(col_names))
    insert_sql = (
        f"INSERT INTO {dataset.table_name} ({', '.join(col_names)}) "
        f"VALUES ({placeholders})"
    )

    total_rows = 0
    for chunk in parse_gz_csv(filepath, dataset, chunk_size=chunk_size):
        values = [_prepare_row(row, dataset) for row in chunk]
        with conn.cursor() as cur:
            psycopg2.extras.execute_batch(cur, insert_sql, values, page_size=chunk_size)
        conn.commit()
        total_rows += len(values)

        if progress_callback:
            progress_callback(dataset.name, total_rows)

    logger.info("Loaded %d rows from %s into %s", total_rows, filepath.name, dataset.table_name)
    return total_rows


def load_dataset(
    conn,
    dataset: DIMEDataset,
    data_dir: Path,
    *,
    chunk_size: int = 10000,
    progress_callback=None,
) -> int:
    """Load all files for a dataset from data_dir.

    Returns total rows loaded.
    """
    files = find_data_files(data_dir, dataset)
    if not files:
        logger.warning("No files found for %s in %s", dataset.name, data_dir)
        return 0

    total = 0
    for filepath in files:
        logger.info("Loading %s (%s)", filepath.name, dataset.name)
        n = load_file(
            conn, filepath, dataset,
            chunk_size=chunk_size,
            progress_callback=progress_callback,
        )
        total += n

    return total


def get_connection(database_url: str | None = None):
    """Create a psycopg2 connection."""
    url = database_url or os.environ.get(
        "DATABASE_URL",
        "postgresql://datatalk:datatalk_dev@localhost:5432/datatalk",
    )
    return psycopg2.connect(url)


def load_all(
    data_dir: Path | str = DEFAULT_DATA_DIR,
    database_url: str | None = None,
    datasets: list[str] | None = None,
    *,
    reset: bool = False,
    chunk_size: int = 10000,
    progress_callback=None,
) -> dict[str, int]:
    """Full import: create schema, load data, create indexes.

    Args:
        data_dir: Directory containing DIME CSV.GZ files.
        database_url: PostgreSQL connection URL.
        datasets: Dataset names to load (default: all found).
        reset: Drop and recreate tables before loading.
        chunk_size: Rows per INSERT batch.
        progress_callback: Called with (dataset_name, rows_so_far).

    Returns:
        Dict mapping dataset name -> rows loaded.
    """
    data_dir = Path(data_dir)
    conn = get_connection(database_url)
    conn.autocommit = False

    try:
        if reset:
            drop_tables(conn)

        create_schema(conn)

        target_names = set(datasets) if datasets else {d.name for d in ALL_DATASETS}
        results: dict[str, int] = {}

        for ds in ALL_DATASETS:
            if ds.name not in target_names:
                continue
            n = load_dataset(
                conn, ds, data_dir,
                chunk_size=chunk_size,
                progress_callback=progress_callback,
            )
            if n > 0:
                results[ds.name] = n

        create_indexes(conn)
        return results

    finally:
        conn.close()
