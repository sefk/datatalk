"""
OpenSecrets data loader for PostgreSQL.

Creates the opensecrets_* tables, loads CSV data with batch inserts,
creates indexes, and handles idempotent loads.  Designed to coexist with
FEC tables in the same database — all tables use the ``opensecrets_`` prefix.
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
import psycopg2
import psycopg2.extras

from backend.datatalk.pipeline.scrapers.opensecrets import (
    DATASETS,
    DATASETS_BY_NAME,
    OpenSecretsDataset,
    find_csv_for_dataset,
    read_csv,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Metadata table — tracks import runs for auditability
# ---------------------------------------------------------------------------
IMPORT_LOG_TABLE = "opensecrets_import_log"

IMPORT_LOG_DDL = f"""
CREATE TABLE IF NOT EXISTS {IMPORT_LOG_TABLE} (
    id              SERIAL PRIMARY KEY,
    dataset         VARCHAR(64) NOT NULL,
    cycle           SMALLINT,
    rows_loaded     INTEGER NOT NULL DEFAULT 0,
    source_file     TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at     TIMESTAMPTZ,
    status          VARCHAR(16) NOT NULL DEFAULT 'running'  -- running | ok | error
);
"""

# ---------------------------------------------------------------------------
# Index definitions (column names are lowercased in Postgres)
# ---------------------------------------------------------------------------
# Each entry: (index_name, table_name, column_expression)
INDEXES = [
    # Candidates
    ("idx_os_cand_cid", "opensecrets_candidates", "cid"),
    ("idx_os_cand_cycle", "opensecrets_candidates", "cycle"),
    ("idx_os_cand_name", "opensecrets_candidates", "firstlastp"),
    ("idx_os_cand_fec", "opensecrets_candidates", "feccandid"),
    ("idx_os_cand_party", "opensecrets_candidates", "party"),
    # PACs
    ("idx_os_pac_pacid", "opensecrets_pacs", "pacid"),
    ("idx_os_pac_cycle", "opensecrets_pacs", "cycle"),
    ("idx_os_pac_primcode", "opensecrets_pacs", "primcode"),
    ("idx_os_pac_ultorg", "opensecrets_pacs", "ultorg"),
    # Individual contributions
    ("idx_os_indiv_cycle", "opensecrets_individual_contributions", "cycle"),
    ("idx_os_indiv_recipid", "opensecrets_individual_contributions", "recipid"),
    ("idx_os_indiv_realcode", "opensecrets_individual_contributions", "realcode"),
    ("idx_os_indiv_date", "opensecrets_individual_contributions", "date"),
    ("idx_os_indiv_state", "opensecrets_individual_contributions", "state"),
    ("idx_os_indiv_contrib", "opensecrets_individual_contributions", "contrib"),
    # PAC to candidates
    ("idx_os_p2c_pacid", "opensecrets_pac_to_candidates", "pacid"),
    ("idx_os_p2c_cid", "opensecrets_pac_to_candidates", "cid"),
    ("idx_os_p2c_cycle", "opensecrets_pac_to_candidates", "cycle"),
    ("idx_os_p2c_realcode", "opensecrets_pac_to_candidates", "realcode"),
    ("idx_os_p2c_date", "opensecrets_pac_to_candidates", "date"),
]


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------

def _column_def(col_name: str, col_type: str) -> str:
    """Build a Postgres column definition with lowercased name."""
    return f"    {col_name.lower()} {col_type}"


def create_table_ddl(dataset: OpenSecretsDataset) -> str:
    """Generate CREATE TABLE IF NOT EXISTS DDL for a dataset."""
    cols = ",\n".join(
        _column_def(name, pg_type) for name, pg_type in dataset.columns
    )
    return f"CREATE TABLE IF NOT EXISTS {dataset.table_name} (\n{cols}\n);"


def create_schema(conn) -> None:
    """Create all OpenSecrets tables and the import log table.

    Safe to call repeatedly — uses CREATE TABLE IF NOT EXISTS.
    """
    with conn.cursor() as cur:
        cur.execute(IMPORT_LOG_DDL)
        for ds in DATASETS:
            ddl = create_table_ddl(ds)
            logger.debug("Executing DDL:\n%s", ddl)
            cur.execute(ddl)
    conn.commit()
    logger.info("OpenSecrets schema created/verified (%d tables)", len(DATASETS) + 1)


def create_indexes(conn) -> None:
    """Create indexes on key columns.  Skips indexes that already exist."""
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


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _coerce_value(value: str, pg_type: str):
    """Coerce a string CSV value to the appropriate Python type for Postgres."""
    if value == "" or value is None:
        return None

    pg_type_upper = pg_type.upper()
    if pg_type_upper == "SMALLINT" or pg_type_upper == "INTEGER":
        try:
            return int(value)
        except ValueError:
            return None
    elif pg_type_upper == "DATE":
        # OpenSecrets dates are typically MM/DD/YYYY
        for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
            try:
                from datetime import datetime as dt
                return dt.strptime(value, fmt).date()
            except ValueError:
                continue
        return None
    elif pg_type_upper.startswith("NUMERIC") or pg_type_upper == "REAL":
        try:
            return float(value)
        except ValueError:
            return None
    else:
        return value


def _prepare_row(row: dict, dataset: OpenSecretsDataset) -> tuple:
    """Convert a dict row to a tuple of Postgres-ready values."""
    values = []
    for col_name, pg_type in dataset.columns:
        raw = row.get(col_name, "")
        values.append(_coerce_value(raw, pg_type))
    return tuple(values)


def _log_import_start(conn, dataset_name: str, cycle: int | None, source_file: str) -> int:
    """Insert a row into the import log and return its id."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            INSERT INTO {IMPORT_LOG_TABLE} (dataset, cycle, source_file, started_at, status)
            VALUES (%s, %s, %s, %s, 'running')
            RETURNING id
            """,
            (dataset_name, cycle, source_file, datetime.now(timezone.utc)),
        )
        row = cur.fetchone()
        import_id = row[0]
    conn.commit()
    return import_id


def _log_import_finish(conn, import_id: int, rows: int, status: str = "ok") -> None:
    """Update the import log row with completion info."""
    with conn.cursor() as cur:
        cur.execute(
            f"""
            UPDATE {IMPORT_LOG_TABLE}
            SET finished_at = %s, rows_loaded = %s, status = %s
            WHERE id = %s
            """,
            (datetime.now(timezone.utc), rows, status, import_id),
        )
    conn.commit()


def load_dataset(
    conn,
    dataset: OpenSecretsDataset,
    rows: list[dict],
    *,
    batch_size: int = 1000,
    cycle: int | None = None,
    source_file: str = "",
    truncate_first: bool = True,
) -> int:
    """Load rows into a dataset's table using batch inserts.

    For idempotent loads, *truncate_first* (default True) deletes existing
    rows for the same cycle before inserting.  If cycle is provided and
    truncate_first is True, only rows matching that cycle are deleted.
    Otherwise the entire table is truncated.

    Returns the number of rows inserted.
    """
    import_id = _log_import_start(conn, dataset.name, cycle, source_file)

    try:
        col_names = [c[0].lower() for c in dataset.columns]
        placeholders = ", ".join(["%s"] * len(col_names))
        insert_sql = (
            f"INSERT INTO {dataset.table_name} ({', '.join(col_names)}) "
            f"VALUES ({placeholders})"
        )

        with conn.cursor() as cur:
            # Idempotent: remove old data
            if truncate_first:
                if cycle is not None:
                    cur.execute(
                        f"DELETE FROM {dataset.table_name} WHERE cycle = %s",
                        (cycle,),
                    )
                    logger.info(
                        "Deleted existing rows for cycle %d in %s",
                        cycle,
                        dataset.table_name,
                    )
                else:
                    cur.execute(f"TRUNCATE TABLE {dataset.table_name}")
                    logger.info("Truncated %s", dataset.table_name)

            # Batch insert
            total = 0
            batch = []
            for row in rows:
                batch.append(_prepare_row(row, dataset))
                if len(batch) >= batch_size:
                    psycopg2.extras.execute_batch(
                        cur, insert_sql, batch, page_size=batch_size
                    )
                    total += len(batch)
                    batch = []

            if batch:
                psycopg2.extras.execute_batch(
                    cur, insert_sql, batch, page_size=batch_size
                )
                total += len(batch)

        conn.commit()
        _log_import_finish(conn, import_id, total, "ok")
        logger.info(
            "Loaded %d rows into %s", total, dataset.table_name
        )
        return total

    except Exception:
        conn.rollback()
        _log_import_finish(conn, import_id, 0, "error")
        raise


def load_from_csv(
    conn,
    data_dir: str | Path,
    dataset_names: list[str] | None = None,
    *,
    cycle: int | None = None,
    batch_size: int = 1000,
    truncate_first: bool = True,
) -> dict[str, int]:
    """Load one or more datasets from CSV files in *data_dir*.

    Args:
        conn: psycopg2 connection.
        data_dir: Directory containing CSV files.
        dataset_names: Datasets to load (default: all that have a matching CSV).
        cycle: Election cycle (used for idempotent delete).
        batch_size: Rows per INSERT batch.
        truncate_first: Delete existing rows before loading.

    Returns:
        Dict mapping dataset name -> rows loaded.
    """
    data_dir = Path(data_dir)
    if not data_dir.is_dir():
        raise FileNotFoundError(f"Data directory not found: {data_dir}")

    targets = dataset_names or [d.name for d in DATASETS]
    results: dict[str, int] = {}

    for name in targets:
        ds = DATASETS_BY_NAME.get(name)
        if ds is None:
            logger.warning("Unknown dataset: %s (skipping)", name)
            continue

        csv_path = find_csv_for_dataset(data_dir, ds)
        if csv_path is None:
            logger.warning(
                "No CSV found for %s in %s (expected %s)",
                name,
                data_dir,
                ds.csv_filename,
            )
            continue

        rows = read_csv(csv_path, ds)
        n = load_dataset(
            conn,
            ds,
            rows,
            batch_size=batch_size,
            cycle=cycle,
            source_file=str(csv_path),
            truncate_first=truncate_first,
        )
        results[name] = n

    return results


def full_import(
    conn,
    data_dir: str | Path,
    *,
    cycle: int | None = None,
    batch_size: int = 1000,
) -> dict[str, int]:
    """Run a complete import: create schema, load CSVs, create indexes.

    This is the main entry point for the CLI script.
    """
    create_schema(conn)
    results = load_from_csv(
        conn,
        data_dir,
        cycle=cycle,
        batch_size=batch_size,
    )
    create_indexes(conn)
    return results
