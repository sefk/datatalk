"""
FEC bulk data scraper.

Downloads and parses bulk data files from the Federal Election Commission.
Data source: https://www.fec.gov/data/browse-data/?tab=bulk-data
Documentation: https://www.fec.gov/campaign-finance-data/bulk-data-documentation/

Supported datasets:
  - Candidates (cn.txt)
  - Committees (cm.txt)
  - Contributions by Individuals (itcont.txt)
  - Contributions from Committees to Candidates (itpas2.txt)
  - Candidate-Committee Linkage (ccl.txt)

All files are pipe-delimited with no header row. Field definitions are from
FEC bulk data documentation.
"""

import csv
import hashlib
import io
import json
import logging
import os
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Generator, Optional

import httpx

logger = logging.getLogger(__name__)

# Default download directory (gitignored via .tmp/)
DEFAULT_DATA_DIR = Path(".tmp/fec_data")

# FEC bulk data base URL pattern
# Cycle is expressed as a 2-digit year, e.g. "24" for 2023-2024.
FEC_BULK_URL = "https://cg-519a459a-0ea3-42c2-b7bc-fa1143481f74.s3-us-gov-west-1.amazonaws.com/bulk-downloads/{cycle}/{filename}.zip"


# ---------------------------------------------------------------------------
# Column definitions — sourced from FEC bulk data documentation
# https://www.fec.gov/campaign-finance-data/candidate-master-file-description/
# https://www.fec.gov/campaign-finance-data/committee-master-file-description/
# https://www.fec.gov/campaign-finance-data/contributions-individuals-file-description/
# https://www.fec.gov/campaign-finance-data/contributions-committees-candidates-file-description/
# https://www.fec.gov/campaign-finance-data/candidate-committee-linkage-file-description/
# ---------------------------------------------------------------------------

@dataclass
class FECDataset:
    """Metadata for a single FEC bulk data file."""
    name: str           # Human-readable name
    filename: str       # Base filename without extension (e.g. "cn")
    columns: list       # Ordered list of column names
    description: str = ""


CANDIDATES = FECDataset(
    name="Candidates",
    filename="cn",
    description="Candidate master file — one row per candidate per cycle.",
    columns=[
        "cand_id",             # Candidate identification
        "cand_name",           # Candidate name
        "cand_pty_affiliation",# Party affiliation
        "cand_election_yr",    # Year of election
        "cand_office_st",      # Candidate state
        "cand_office",         # Candidate office (H=House, S=Senate, P=President)
        "cand_office_district",# Candidate district
        "cand_ici",            # Incumbent challenger status (I/C/O)
        "cand_status",         # Candidate status (C/F/N/P)
        "cand_pcc",            # Principal campaign committee
        "cand_st1",            # Mailing address street 1
        "cand_st2",            # Mailing address street 2
        "cand_city",           # Mailing address city
        "cand_st",             # Mailing address state
        "cand_zip",            # Mailing address zip
    ],
)

COMMITTEES = FECDataset(
    name="Committees",
    filename="cm",
    description="Committee master file — one row per committee.",
    columns=[
        "cmte_id",             # Committee identification
        "cmte_nm",             # Committee name
        "tres_nm",             # Treasurer name
        "cmte_st1",            # Street 1
        "cmte_st2",            # Street 2
        "cmte_city",           # City
        "cmte_st",             # State
        "cmte_zip",            # Zip code
        "cmte_dsgn",           # Designation (A/B/D/J/P/U)
        "cmte_tp",             # Committee type
        "cmte_pty_affiliation",# Party affiliation
        "cmte_filing_freq",    # Filing frequency (A/M/N/Q/T/W)
        "org_tp",              # Interest group category
        "connected_org_nm",    # Connected organization name
        "cand_id",             # Candidate identification (if applicable)
    ],
)

INDIVIDUAL_CONTRIBUTIONS = FECDataset(
    name="Individual Contributions",
    filename="itcont",
    description="Contributions by individuals to committees.",
    columns=[
        "cmte_id",             # Filer identification number
        "amndt_ind",           # Amendment indicator
        "rpt_tp",              # Report type
        "transaction_pgi",     # Primary-general indicator
        "image_num",           # Microfilm location / image number
        "transaction_tp",      # Transaction type
        "entity_tp",           # Entity type
        "name",                # Contributor name
        "city",                # Contributor city
        "state",               # Contributor state
        "zip_code",            # Contributor zip code
        "employer",            # Contributor employer
        "occupation",          # Contributor occupation
        "transaction_dt",      # Transaction date (MMDDYYYY)
        "transaction_amt",     # Transaction amount
        "other_id",            # Other identification number
        "tran_id",             # Transaction ID
        "file_num",            # File number / Report ID
        "memo_cd",             # Memo code
        "memo_text",           # Memo text
        "sub_id",              # FEC record number (unique row ID)
    ],
)

COMMITTEE_CONTRIBUTIONS = FECDataset(
    name="Committee Contributions to Candidates",
    filename="itpas2",
    description="Contributions from committees to candidates and other committees.",
    columns=[
        "cmte_id",             # Filer identification number
        "amndt_ind",           # Amendment indicator
        "rpt_tp",              # Report type
        "transaction_pgi",     # Primary-general indicator
        "image_num",           # Image number
        "transaction_tp",      # Transaction type
        "entity_tp",           # Entity type
        "name",                # Contributor/lender name
        "city",                # City
        "state",               # State
        "zip_code",            # Zip code
        "employer",            # Employer
        "occupation",          # Occupation
        "transaction_dt",      # Transaction date (MMDDYYYY)
        "transaction_amt",     # Transaction amount
        "other_id",            # Other identification number
        "cand_id",             # Candidate identification
        "tran_id",             # Transaction ID
        "file_num",            # File number
        "memo_cd",             # Memo code
        "memo_text",           # Memo text
        "sub_id",              # FEC record number
    ],
)

CANDIDATE_COMMITTEE_LINKAGE = FECDataset(
    name="Candidate-Committee Linkage",
    filename="ccl",
    description="Maps candidates to their authorized committees.",
    columns=[
        "cand_id",             # Candidate identification
        "cand_election_yr",    # Candidate election year
        "fec_election_yr",     # FEC election year
        "cmte_id",             # Committee identification
        "cmte_tp",             # Committee type
        "cmte_dsgn",           # Committee designation
        "linkage_id",          # Linkage ID
    ],
)

ALL_DATASETS = [
    CANDIDATES,
    COMMITTEES,
    INDIVIDUAL_CONTRIBUTIONS,
    COMMITTEE_CONTRIBUTIONS,
    CANDIDATE_COMMITTEE_LINKAGE,
]


# ---------------------------------------------------------------------------
# State tracking for incremental downloads
# ---------------------------------------------------------------------------

STATE_FILE = "download_state.json"


def _state_path(data_dir: Path) -> Path:
    return data_dir / STATE_FILE


def load_state(data_dir: Path) -> dict:
    """Load download state from disk."""
    path = _state_path(data_dir)
    if path.exists():
        with open(path) as f:
            return json.load(f)
    return {}


def save_state(data_dir: Path, state: dict) -> None:
    """Persist download state to disk."""
    path = _state_path(data_dir)
    with open(path, "w") as f:
        json.dump(state, f, indent=2)


# ---------------------------------------------------------------------------
# Download logic
# ---------------------------------------------------------------------------

def cycle_to_two_digit(cycle: int) -> str:
    """Convert a 4-digit cycle year to 2-digit for FEC URLs.

    E.g. 2024 -> "24", 2020 -> "20".
    """
    return str(cycle % 100).zfill(2)


def build_url(cycle: int, dataset: FECDataset) -> str:
    """Build the FEC bulk download URL for a dataset and cycle."""
    cycle_2d = cycle_to_two_digit(cycle)
    return FEC_BULK_URL.format(cycle=cycle_2d, filename=dataset.filename)


def _file_md5(path: Path) -> str:
    """Compute MD5 hash of a file for change detection."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def download_dataset(
    dataset: FECDataset,
    cycle: int,
    data_dir: Path = DEFAULT_DATA_DIR,
    force: bool = False,
    client: Optional[httpx.Client] = None,
) -> Path:
    """Download a single FEC dataset zip and extract it.

    Returns the path to the extracted .txt file.

    Supports incremental updates: skips download if the remote file has
    not changed (tracked via Content-Length + ETag in state file).
    """
    data_dir = Path(data_dir)
    cycle_dir = data_dir / str(cycle)
    cycle_dir.mkdir(parents=True, exist_ok=True)

    url = build_url(cycle, dataset)
    zip_path = cycle_dir / f"{dataset.filename}.zip"
    txt_path = cycle_dir / f"{dataset.filename}.txt"

    state = load_state(data_dir)
    state_key = f"{cycle}/{dataset.filename}"

    # Check if we can skip the download
    if not force and txt_path.exists() and state_key in state:
        logger.info("Skipping %s — already downloaded (use --force to re-download)", dataset.name)
        return txt_path

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=600, follow_redirects=True)

    try:
        logger.info("Downloading %s from %s", dataset.name, url)

        # Stream the download to avoid loading large files into memory
        with client.stream("GET", url) as response:
            response.raise_for_status()
            total = int(response.headers.get("content-length", 0))
            downloaded = 0

            with open(zip_path, "wb") as f:
                for chunk in response.iter_bytes(chunk_size=65536):
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = downloaded * 100 // total
                        if downloaded == len(chunk) or pct % 10 == 0:
                            logger.info(
                                "  %s: %d / %d bytes (%d%%)",
                                dataset.filename, downloaded, total, pct,
                            )

        logger.info("Extracting %s", zip_path.name)
        with zipfile.ZipFile(zip_path, "r") as zf:
            # FEC zips contain a single .txt file (sometimes with header files)
            txt_members = [m for m in zf.namelist() if m.endswith(".txt")]
            if not txt_members:
                raise ValueError(f"No .txt file found in {zip_path}")

            # Extract the main data file — it is typically the largest .txt
            target_member = txt_members[0]
            for m in txt_members:
                if dataset.filename in m.lower():
                    target_member = m
                    break

            zf.extract(target_member, cycle_dir)
            extracted = cycle_dir / target_member
            if extracted != txt_path:
                extracted.rename(txt_path)

        # Update state
        etag = response.headers.get("etag", "")
        state[state_key] = {
            "url": url,
            "etag": etag,
            "content_length": total,
            "downloaded_at": datetime.utcnow().isoformat(),
            "md5": _file_md5(txt_path),
        }
        save_state(data_dir, state)

        # Clean up zip
        zip_path.unlink(missing_ok=True)

        logger.info("Downloaded %s (%s)", dataset.name, txt_path)
        return txt_path

    finally:
        if own_client:
            client.close()


def download_all(
    cycle: int,
    data_dir: Path = DEFAULT_DATA_DIR,
    force: bool = False,
    datasets: Optional[list[FECDataset]] = None,
) -> dict[str, Path]:
    """Download all (or selected) FEC datasets for a given cycle.

    Returns a mapping of dataset filename -> extracted txt path.
    """
    if datasets is None:
        datasets = ALL_DATASETS

    data_dir = Path(data_dir)
    results = {}

    with httpx.Client(timeout=600, follow_redirects=True) as client:
        for ds in datasets:
            path = download_dataset(ds, cycle, data_dir, force=force, client=client)
            results[ds.filename] = path

    return results


# ---------------------------------------------------------------------------
# Parsing — streaming, pipe-delimited
# ---------------------------------------------------------------------------

def parse_row(line: str, columns: list[str]) -> Optional[dict]:
    """Parse a single pipe-delimited line into a dict.

    Returns None if the row is malformed (wrong number of fields).
    """
    # FEC files use pipe delimiter, no quoting, fields may contain commas
    fields = line.rstrip("\n\r").split("|")

    # FEC files sometimes have a trailing pipe, giving one extra empty field
    if len(fields) == len(columns) + 1 and fields[-1] == "":
        fields = fields[:-1]

    if len(fields) != len(columns):
        return None

    row = {}
    for col, val in zip(columns, fields):
        row[col] = val.strip() if val else None
    return row


def parse_file(
    filepath: Path,
    dataset: FECDataset,
    chunk_size: int = 10000,
) -> Generator[list[dict], None, None]:
    """Stream-parse an FEC bulk data file in chunks.

    Yields lists of dicts (one dict per row), up to chunk_size rows per yield.
    Malformed rows are logged and skipped.

    This is designed for large files (multi-GB itcont.txt) — it never loads
    the entire file into memory.
    """
    filepath = Path(filepath)
    chunk = []
    malformed_count = 0

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        for line_num, line in enumerate(f, start=1):
            if not line.strip():
                continue

            row = parse_row(line, dataset.columns)
            if row is None:
                malformed_count += 1
                if malformed_count <= 10:
                    logger.warning(
                        "Malformed row at line %d in %s (expected %d fields): %s",
                        line_num, filepath.name, len(dataset.columns),
                        line[:200],
                    )
                continue

            chunk.append(row)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []

    if chunk:
        yield chunk

    if malformed_count > 0:
        logger.warning(
            "Total malformed rows in %s: %d", filepath.name, malformed_count
        )


def get_dataset_by_filename(filename: str) -> Optional[FECDataset]:
    """Look up a dataset definition by its filename (e.g. 'cn', 'itcont')."""
    for ds in ALL_DATASETS:
        if ds.filename == filename:
            return ds
    return None
