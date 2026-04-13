"""
FEC data loader — creates PostgreSQL schema and loads parsed FEC data.

Uses the creator_role / select_user role pattern from V1:
  - creator_role: has DDL privileges, used for schema creation and data loading
  - select_user:  read-only role, used by the query engine

Designed for idempotent loads: tables are dropped and recreated on each run.
Uses batch inserts with streaming reads for large files.
"""

import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import psycopg2
from psycopg2 import sql as pgsql
from psycopg2.extras import execute_values

from backend.datatalk.pipeline.scrapers.fec import (
    ALL_DATASETS,
    CANDIDATES,
    CANDIDATE_COMMITTEE_LINKAGE,
    COMMITTEES,
    COMMITTEE_CONTRIBUTIONS,
    FECDataset,
    INDIVIDUAL_CONTRIBUTIONS,
    parse_file,
)

logger = logging.getLogger(__name__)

DEFAULT_DATABASE_URL = "postgresql://creator_role:creator_role@localhost:5432/datatalk"

# Batch size for inserts — balances memory vs. round-trip overhead
INSERT_BATCH_SIZE = 5000


# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------

# SQL type for each column, keyed by dataset filename then column name.
# Types are chosen to match FEC documentation and optimize query performance.

COLUMN_TYPES: dict[str, dict[str, str]] = {
    "cn": {
        "cand_id": "VARCHAR(9) NOT NULL",
        "cand_name": "VARCHAR(200)",
        "cand_pty_affiliation": "VARCHAR(3)",
        "cand_election_yr": "SMALLINT",
        "cand_office_st": "VARCHAR(2)",
        "cand_office": "VARCHAR(1)",
        "cand_office_district": "VARCHAR(2)",
        "cand_ici": "VARCHAR(1)",
        "cand_status": "VARCHAR(1)",
        "cand_pcc": "VARCHAR(9)",
        "cand_st1": "VARCHAR(34)",
        "cand_st2": "VARCHAR(34)",
        "cand_city": "VARCHAR(30)",
        "cand_st": "VARCHAR(2)",
        "cand_zip": "VARCHAR(9)",
    },
    "cm": {
        "cmte_id": "VARCHAR(9) NOT NULL",
        "cmte_nm": "VARCHAR(200)",
        "tres_nm": "VARCHAR(90)",
        "cmte_st1": "VARCHAR(34)",
        "cmte_st2": "VARCHAR(34)",
        "cmte_city": "VARCHAR(30)",
        "cmte_st": "VARCHAR(2)",
        "cmte_zip": "VARCHAR(9)",
        "cmte_dsgn": "VARCHAR(1)",
        "cmte_tp": "VARCHAR(1)",
        "cmte_pty_affiliation": "VARCHAR(3)",
        "cmte_filing_freq": "VARCHAR(1)",
        "org_tp": "VARCHAR(1)",
        "connected_org_nm": "VARCHAR(200)",
        "cand_id": "VARCHAR(9)",
    },
    "itcont": {
        "cmte_id": "VARCHAR(9) NOT NULL",
        "amndt_ind": "VARCHAR(1)",
        "rpt_tp": "VARCHAR(3)",
        "transaction_pgi": "VARCHAR(5)",
        "image_num": "VARCHAR(18)",
        "transaction_tp": "VARCHAR(3)",
        "entity_tp": "VARCHAR(3)",
        "name": "VARCHAR(200)",
        "city": "VARCHAR(30)",
        "state": "VARCHAR(2)",
        "zip_code": "VARCHAR(9)",
        "employer": "VARCHAR(38)",
        "occupation": "VARCHAR(38)",
        "transaction_dt": "VARCHAR(8)",
        "transaction_amt": "NUMERIC(14,2)",
        "other_id": "VARCHAR(9)",
        "tran_id": "VARCHAR(32)",
        "file_num": "BIGINT",
        "memo_cd": "VARCHAR(1)",
        "memo_text": "VARCHAR(100)",
        "sub_id": "BIGINT NOT NULL",
    },
    "itpas2": {
        "cmte_id": "VARCHAR(9) NOT NULL",
        "amndt_ind": "VARCHAR(1)",
        "rpt_tp": "VARCHAR(3)",
        "transaction_pgi": "VARCHAR(5)",
        "image_num": "VARCHAR(18)",
        "transaction_tp": "VARCHAR(3)",
        "entity_tp": "VARCHAR(3)",
        "name": "VARCHAR(200)",
        "city": "VARCHAR(30)",
        "state": "VARCHAR(2)",
        "zip_code": "VARCHAR(9)",
        "employer": "VARCHAR(38)",
        "occupation": "VARCHAR(38)",
        "transaction_dt": "VARCHAR(8)",
        "transaction_amt": "NUMERIC(14,2)",
        "other_id": "VARCHAR(9)",
        "cand_id": "VARCHAR(9)",
        "tran_id": "VARCHAR(32)",
        "file_num": "BIGINT",
        "memo_cd": "VARCHAR(1)",
        "memo_text": "VARCHAR(100)",
        "sub_id": "BIGINT NOT NULL",
    },
    "ccl": {
        "cand_id": "VARCHAR(9) NOT NULL",
        "cand_election_yr": "SMALLINT",
        "fec_election_yr": "SMALLINT",
        "cmte_id": "VARCHAR(9) NOT NULL",
        "cmte_tp": "VARCHAR(1)",
        "cmte_dsgn": "VARCHAR(1)",
        "linkage_id": "BIGINT NOT NULL",
    },
}

# Column descriptions for SQL comments (aids LLM schema understanding)
COLUMN_DESCRIPTIONS: dict[str, dict[str, str]] = {
    "cn": {
        "cand_id": "Candidate identification number",
        "cand_name": "Candidate name",
        "cand_pty_affiliation": "Party affiliation (e.g. DEM, REP, LIB)",
        "cand_election_yr": "Year of election",
        "cand_office_st": "State of candidacy",
        "cand_office": "Office sought: H=House, S=Senate, P=President",
        "cand_office_district": "Congressional district number",
        "cand_ici": "Incumbent/challenger/open status: I=Incumbent, C=Challenger, O=Open seat",
        "cand_status": "Candidate status: C=Statutory candidate, F=Statutory candidate for future election, N=Not yet a statutory candidate, P=Statutory candidate in prior cycle",
        "cand_pcc": "Principal campaign committee ID",
        "cand_st1": "Mailing address street line 1",
        "cand_st2": "Mailing address street line 2",
        "cand_city": "Mailing address city",
        "cand_st": "Mailing address state abbreviation",
        "cand_zip": "Mailing address zip code",
    },
    "cm": {
        "cmte_id": "Committee identification number",
        "cmte_nm": "Committee name",
        "tres_nm": "Treasurer name",
        "cmte_st1": "Street address line 1",
        "cmte_st2": "Street address line 2",
        "cmte_city": "City",
        "cmte_st": "State",
        "cmte_zip": "Zip code",
        "cmte_dsgn": "Committee designation: A=Authorized by candidate, B=Lobbyist/Registrant PAC, D=Leadership PAC, J=Joint fundraiser, P=Principal campaign committee, U=Unauthorized",
        "cmte_tp": "Committee type",
        "cmte_pty_affiliation": "Party affiliation",
        "cmte_filing_freq": "Filing frequency: A=Administratively terminated, D=Debt, M=Monthly filer, Q=Quarterly filer, T=Terminated, W=Waived",
        "org_tp": "Interest group category: C=Corporation, L=Labor organization, M=Membership organization, T=Trade association, V=Cooperative, W=Corporation without capital stock",
        "connected_org_nm": "Connected organization name",
        "cand_id": "Candidate ID linked to the committee (when applicable)",
    },
    "itcont": {
        "cmte_id": "Filer identification number (committee receiving the contribution)",
        "amndt_ind": "Amendment indicator: N=New, A=Amendment, T=Termination",
        "rpt_tp": "Report type (e.g. Q1=April quarterly, 12G=pre-general)",
        "transaction_pgi": "Primary/general indicator: P=Primary, G=General, O=Other, C=Convention, R=Runoff, S=Special, E=Recount",
        "image_num": "Microfilm location or image number",
        "transaction_tp": "Transaction type code (e.g. 15=Contribution, 15E=Earmarked)",
        "entity_tp": "Entity type: IND=Individual, COM=Committee, ORG=Organization, PAC=Political action committee, PTY=Party organization",
        "name": "Contributor/lender/transfer name",
        "city": "Contributor city",
        "state": "Contributor state",
        "zip_code": "Contributor zip code",
        "employer": "Contributor employer",
        "occupation": "Contributor occupation",
        "transaction_dt": "Transaction date (MMDDYYYY format)",
        "transaction_amt": "Transaction amount in dollars (negative for refunds)",
        "other_id": "Other identification number for non-individual contributors",
        "tran_id": "Transaction ID assigned by filer",
        "file_num": "File number / report ID",
        "memo_cd": "Memo code: X indicates the amount is NOT included in total",
        "memo_text": "Memo text providing additional transaction information",
        "sub_id": "FEC record number (unique identifier for each row)",
    },
    "itpas2": {
        "cmte_id": "Filer identification number (committee making the contribution)",
        "amndt_ind": "Amendment indicator: N=New, A=Amendment, T=Termination",
        "rpt_tp": "Report type",
        "transaction_pgi": "Primary/general indicator",
        "image_num": "Microfilm location or image number",
        "transaction_tp": "Transaction type code (e.g. 24A=Independent expenditure against, 24K=Contribution to committee)",
        "entity_tp": "Entity type",
        "name": "Contributor/lender name",
        "city": "City",
        "state": "State",
        "zip_code": "Zip code",
        "employer": "Employer",
        "occupation": "Occupation",
        "transaction_dt": "Transaction date (MMDDYYYY format)",
        "transaction_amt": "Transaction amount in dollars",
        "other_id": "Other identification number",
        "cand_id": "Candidate identification number receiving the contribution",
        "tran_id": "Transaction ID",
        "file_num": "File number / report ID",
        "memo_cd": "Memo code",
        "memo_text": "Memo text",
        "sub_id": "FEC record number (unique identifier)",
    },
    "ccl": {
        "cand_id": "Candidate identification number",
        "cand_election_yr": "Candidate election year",
        "fec_election_yr": "FEC election year",
        "cmte_id": "Committee identification number",
        "cmte_tp": "Committee type",
        "cmte_dsgn": "Committee designation",
        "linkage_id": "Linkage ID (unique identifier)",
    },
}

# Table names in Postgres — prefixed with fec_ for clarity
TABLE_NAMES = {
    "cn": "fec_candidates",
    "cm": "fec_committees",
    "itcont": "fec_individual_contributions",
    "itpas2": "fec_committee_contributions",
    "ccl": "fec_candidate_committee_linkage",
}

# Primary keys
PRIMARY_KEYS = {
    "cn": ["cand_id", "cand_election_yr"],
    "cm": ["cmte_id"],
    "itcont": ["sub_id"],
    "itpas2": ["sub_id"],
    "ccl": ["linkage_id"],
}

# Indexes to create (besides primary keys)
INDEXES: dict[str, list[tuple[str, list[str]]]] = {
    "cn": [
        ("idx_fec_candidates_name", ["cand_name"]),
        ("idx_fec_candidates_state", ["cand_office_st"]),
        ("idx_fec_candidates_office", ["cand_office"]),
        ("idx_fec_candidates_year", ["cand_election_yr"]),
        ("idx_fec_candidates_party", ["cand_pty_affiliation"]),
        ("idx_fec_candidates_pcc", ["cand_pcc"]),
    ],
    "cm": [
        ("idx_fec_committees_name", ["cmte_nm"]),
        ("idx_fec_committees_state", ["cmte_st"]),
        ("idx_fec_committees_type", ["cmte_tp"]),
        ("idx_fec_committees_party", ["cmte_pty_affiliation"]),
        ("idx_fec_committees_cand", ["cand_id"]),
    ],
    "itcont": [
        ("idx_fec_indiv_cmte", ["cmte_id"]),
        ("idx_fec_indiv_state", ["state"]),
        ("idx_fec_indiv_date", ["transaction_dt"]),
        ("idx_fec_indiv_name", ["name"]),
        ("idx_fec_indiv_employer", ["employer"]),
        ("idx_fec_indiv_zip", ["zip_code"]),
    ],
    "itpas2": [
        ("idx_fec_cmte_contrib_cmte", ["cmte_id"]),
        ("idx_fec_cmte_contrib_cand", ["cand_id"]),
        ("idx_fec_cmte_contrib_date", ["transaction_dt"]),
        ("idx_fec_cmte_contrib_other", ["other_id"]),
    ],
    "ccl": [
        ("idx_fec_ccl_cand", ["cand_id"]),
        ("idx_fec_ccl_cmte", ["cmte_id"]),
        ("idx_fec_ccl_year", ["cand_election_yr"]),
    ],
}


# ---------------------------------------------------------------------------
# SQL generation
# ---------------------------------------------------------------------------

def generate_create_table_sql(dataset: FECDataset) -> str:
    """Generate the CREATE TABLE statement for an FEC dataset.

    Includes column comments as SQL line comments for LLM schema exploration.
    """
    table_name = TABLE_NAMES[dataset.filename]
    col_types = COLUMN_TYPES[dataset.filename]
    col_descs = COLUMN_DESCRIPTIONS.get(dataset.filename, {})
    pk_cols = PRIMARY_KEYS[dataset.filename]

    lines = [f'CREATE TABLE IF NOT EXISTS "{table_name}" (']
    col_lines = []
    for col in dataset.columns:
        ctype = col_types[col]
        desc = col_descs.get(col, "")
        comment = f"  -- {desc}" if desc else ""
        col_lines.append(f'    "{col}" {ctype}{comment}')

    # Add primary key constraint
    pk_str = ", ".join(f'"{c}"' for c in pk_cols)
    col_lines.append(f"    PRIMARY KEY ({pk_str})")

    lines.append(",\n".join(col_lines))
    lines.append(");")

    return "\n".join(lines)


def generate_drop_table_sql(dataset: FECDataset) -> str:
    table_name = TABLE_NAMES[dataset.filename]
    return f'DROP TABLE IF EXISTS "{table_name}" CASCADE;'


def generate_index_sql(dataset: FECDataset) -> list[str]:
    """Generate CREATE INDEX statements for an FEC dataset."""
    table_name = TABLE_NAMES[dataset.filename]
    index_defs = INDEXES.get(dataset.filename, [])
    stmts = []
    for idx_name, idx_cols in index_defs:
        cols = ", ".join(f'"{c}"' for c in idx_cols)
        stmts.append(
            f'CREATE INDEX IF NOT EXISTS "{idx_name}" ON "{table_name}" ({cols});'
        )
    return stmts


def generate_grant_sql(dataset: FECDataset, role: str = "select_user") -> str:
    """Generate GRANT SELECT for the read-only role."""
    table_name = TABLE_NAMES[dataset.filename]
    return f'GRANT SELECT ON "{table_name}" TO {role};'


def generate_full_schema_sql() -> str:
    """Generate the full DDL for all FEC tables (for review / testing)."""
    parts = []
    for ds in ALL_DATASETS:
        parts.append(generate_drop_table_sql(ds))
        parts.append(generate_create_table_sql(ds))
        parts.extend(generate_index_sql(ds))
        parts.append(generate_grant_sql(ds))
        parts.append("")  # blank line separator
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Database connection
# ---------------------------------------------------------------------------

def get_connection(database_url: Optional[str] = None):
    """Create a psycopg2 connection from a DATABASE_URL.

    Falls back to DEFAULT_DATABASE_URL if not provided.
    """
    url = database_url or os.environ.get("DATABASE_URL", DEFAULT_DATABASE_URL)
    return psycopg2.connect(url)


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _coerce_value(val: Optional[str], col_type: str):
    """Coerce a string value to the appropriate Python type for insertion."""
    if val is None or val == "":
        return None

    col_type_upper = col_type.upper()

    if "SMALLINT" in col_type_upper or "INT" in col_type_upper or "BIGINT" in col_type_upper:
        try:
            return int(val)
        except (ValueError, TypeError):
            return None

    if "NUMERIC" in col_type_upper or "FLOAT" in col_type_upper or "DECIMAL" in col_type_upper:
        try:
            return float(val)
        except (ValueError, TypeError):
            return None

    return val


def load_dataset(
    dataset: FECDataset,
    filepath: Path,
    database_url: Optional[str] = None,
    chunk_size: int = INSERT_BATCH_SIZE,
    progress_callback=None,
) -> int:
    """Load a single FEC dataset into PostgreSQL.

    This is idempotent: drops and recreates the table on each run.
    Uses batch inserts via execute_values for performance.

    Returns the number of rows inserted.
    """
    table_name = TABLE_NAMES[dataset.filename]
    col_types = COLUMN_TYPES[dataset.filename]

    conn = get_connection(database_url)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # Drop and recreate table
        logger.info("Creating table %s", table_name)
        cur.execute(generate_drop_table_sql(dataset))
        cur.execute(generate_create_table_sql(dataset))
        conn.commit()

        # Load data in chunks
        total_rows = 0
        columns = dataset.columns
        col_list = ", ".join(f'"{c}"' for c in columns)
        insert_template = f'INSERT INTO "{table_name}" ({col_list}) VALUES %s'

        # We use ON CONFLICT DO NOTHING for safety, but since we dropped the
        # table this should not trigger unless there are duplicate rows in source.
        pk_cols = PRIMARY_KEYS[dataset.filename]
        pk_str = ", ".join(f'"{c}"' for c in pk_cols)
        insert_sql = f'{insert_template} ON CONFLICT ({pk_str}) DO NOTHING'

        logger.info("Loading data into %s from %s", table_name, filepath)

        for chunk in parse_file(filepath, dataset, chunk_size=chunk_size):
            values = []
            for row in chunk:
                tup = tuple(
                    _coerce_value(row.get(col), col_types[col])
                    for col in columns
                )
                values.append(tup)

            if values:
                execute_values(cur, insert_sql, values, page_size=chunk_size)
                conn.commit()
                total_rows += len(values)

                if progress_callback:
                    progress_callback(dataset.name, total_rows)
                elif total_rows % 100000 == 0:
                    logger.info("  %s: %d rows loaded", table_name, total_rows)

        # Create indexes after data load (faster than indexing during inserts)
        logger.info("Creating indexes for %s", table_name)
        for idx_sql in generate_index_sql(dataset):
            cur.execute(idx_sql)
        conn.commit()

        # Grant select to read-only role
        try:
            cur.execute(generate_grant_sql(dataset))
            conn.commit()
        except psycopg2.Error as e:
            # select_user role may not exist in dev environments
            conn.rollback()
            logger.warning("Could not grant SELECT to select_user: %s", e)

        logger.info("Loaded %d rows into %s", total_rows, table_name)
        return total_rows

    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()


def load_all(
    cycle: int,
    data_dir: Path,
    database_url: Optional[str] = None,
    datasets: Optional[list[FECDataset]] = None,
    progress_callback=None,
) -> dict[str, int]:
    """Load all FEC datasets for a given cycle.

    Returns a mapping of table name -> rows loaded.
    """
    if datasets is None:
        datasets = ALL_DATASETS

    data_dir = Path(data_dir)
    cycle_dir = data_dir / str(cycle)
    results = {}

    for ds in datasets:
        filepath = cycle_dir / f"{ds.filename}.txt"
        if not filepath.exists():
            logger.warning("Data file not found: %s — skipping", filepath)
            continue

        rows = load_dataset(
            ds, filepath,
            database_url=database_url,
            progress_callback=progress_callback,
        )
        results[TABLE_NAMES[ds.filename]] = rows

    return results


def create_schema_only(database_url: Optional[str] = None) -> None:
    """Create all FEC tables without loading any data.

    Useful for testing or setting up a fresh database.
    """
    conn = get_connection(database_url)
    conn.autocommit = True
    cur = conn.cursor()

    try:
        for ds in ALL_DATASETS:
            cur.execute(generate_drop_table_sql(ds))
            cur.execute(generate_create_table_sql(ds))
            for idx_sql in generate_index_sql(ds):
                cur.execute(idx_sql)
            try:
                cur.execute(generate_grant_sql(ds))
            except psycopg2.Error:
                pass  # select_user may not exist
        logger.info("Schema created for all FEC tables")
    finally:
        cur.close()
        conn.close()
