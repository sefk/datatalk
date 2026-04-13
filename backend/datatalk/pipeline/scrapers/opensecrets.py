"""
OpenSecrets bulk data scraper/downloader.

Defines the expected OpenSecrets data schemas and provides functions to:
- Download bulk data from OpenSecrets (requires API key / researcher access)
- Load from local CSV files (for development with sample data)

OpenSecrets bulk data: https://www.opensecrets.org/open-data/bulk-data
"""

import csv
import logging
import os
from dataclasses import dataclass
from pathlib import Path

import requests  # noqa: F401 — will be used once download is implemented

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema definitions
# ---------------------------------------------------------------------------
# These mirror the OpenSecrets bulk-data CSV layouts.  Column names use the
# canonical OpenSecrets names (CamelCase) so raw CSV files can be loaded
# without renaming.  The Postgres loader lowercases them.

CANDIDATE_COLUMNS = [
    ("Cycle", "SMALLINT"),
    ("FECCandID", "VARCHAR(9)"),
    ("CID", "VARCHAR(9)"),          # OpenSecrets candidate ID (NXXXXXXXX)
    ("FirstLastP", "VARCHAR(50)"),   # "Last, First" display name
    ("Party", "VARCHAR(1)"),
    ("DistIDRunFor", "VARCHAR(4)"),  # e.g. "CA12", "OHS1"
    ("DistIDCurr", "VARCHAR(4)"),
    ("CurrCand", "VARCHAR(1)"),      # Y/N
    ("CycleCand", "VARCHAR(1)"),     # Y/N
    ("CRPICO", "VARCHAR(1)"),        # I=Incumbent, C=Challenger, O=Open
    ("RecipCode", "VARCHAR(2)"),
    ("NoPacs", "VARCHAR(1)"),        # candidate pledged no PAC money
]

PAC_COLUMNS = [
    ("Cycle", "SMALLINT"),
    ("PACid", "VARCHAR(9)"),         # OpenSecrets committee ID (CXXXXXXXX)
    ("PACShort", "VARCHAR(50)"),     # short display name
    ("Affiliate", "VARCHAR(50)"),
    ("Ultorg", "VARCHAR(50)"),       # ultimate parent org
    ("RecipID", "VARCHAR(9)"),
    ("RecipCode", "VARCHAR(2)"),
    ("FECCandID", "VARCHAR(9)"),
    ("Party", "VARCHAR(1)"),
    ("PrimCode", "VARCHAR(5)"),      # primary industry/ideology code
    ("Source", "VARCHAR(5)"),
    ("Sensitive", "VARCHAR(1)"),
    ("IsSensitive", "VARCHAR(1)"),
    ("Active", "SMALLINT"),
]

INDIVIDUAL_CONTRIBUTIONS_COLUMNS = [
    ("Cycle", "SMALLINT"),
    ("FECTransID", "VARCHAR(19)"),
    ("ContribID", "VARCHAR(12)"),
    ("Contrib", "VARCHAR(50)"),       # donor name
    ("RecipID", "VARCHAR(9)"),
    ("Orgname", "VARCHAR(50)"),
    ("UltOrg", "VARCHAR(50)"),
    ("RealCode", "VARCHAR(5)"),       # industry/ideology code
    ("Date", "DATE"),
    ("Amount", "INTEGER"),
    ("Street", "VARCHAR(40)"),
    ("City", "VARCHAR(18)"),
    ("State", "VARCHAR(2)"),
    ("Zip", "VARCHAR(5)"),
    ("RecipCode", "VARCHAR(2)"),
    ("Type", "VARCHAR(3)"),
    ("CmteID", "VARCHAR(9)"),
    ("OtherID", "VARCHAR(9)"),
    ("Gender", "VARCHAR(1)"),
    ("Microfilm", "VARCHAR(11)"),
    ("Occupation", "VARCHAR(38)"),
    ("Employer", "VARCHAR(38)"),
    ("Source", "VARCHAR(5)"),
]

PAC_TO_CANDIDATE_COLUMNS = [
    ("Cycle", "SMALLINT"),
    ("FECRecNo", "VARCHAR(19)"),
    ("PACID", "VARCHAR(9)"),
    ("CID", "VARCHAR(9)"),
    ("Amount", "INTEGER"),
    ("Date", "DATE"),
    ("RealCode", "VARCHAR(5)"),
    ("Type", "VARCHAR(3)"),
    ("DI", "VARCHAR(1)"),             # D=Direct, I=Independent expenditure
    ("FECCandID", "VARCHAR(9)"),
]


@dataclass
class OpenSecretsDataset:
    """Metadata for one OpenSecrets bulk-data file."""

    name: str                        # e.g. "candidates", "pacs"
    table_name: str                  # Postgres table (with opensecrets_ prefix)
    columns: list                    # list of (col_name, pg_type) tuples
    csv_filename: str                # expected CSV filename in the data dir
    description: str = ""


# Registry of all datasets we handle
DATASETS: list[OpenSecretsDataset] = [
    OpenSecretsDataset(
        name="candidates",
        table_name="opensecrets_candidates",
        columns=CANDIDATE_COLUMNS,
        csv_filename="cands.csv",
        description="Enriched candidate records with party, district, winner status",
    ),
    OpenSecretsDataset(
        name="pacs",
        table_name="opensecrets_pacs",
        columns=PAC_COLUMNS,
        csv_filename="pacs.csv",
        description="PAC profiles with industry/ideology classifications",
    ),
    OpenSecretsDataset(
        name="individual_contributions",
        table_name="opensecrets_individual_contributions",
        columns=INDIVIDUAL_CONTRIBUTIONS_COLUMNS,
        csv_filename="indivs.csv",
        description="Individual contributions with employer/industry coding",
    ),
    OpenSecretsDataset(
        name="pac_to_candidates",
        table_name="opensecrets_pac_to_candidates",
        columns=PAC_TO_CANDIDATE_COLUMNS,
        csv_filename="pac_to_cand.csv",
        description="PAC contributions to candidates with industry breakdown",
    ),
]

DATASETS_BY_NAME: dict[str, OpenSecretsDataset] = {d.name: d for d in DATASETS}


# ---------------------------------------------------------------------------
# CSV reader
# ---------------------------------------------------------------------------

def read_csv(filepath: str | Path, dataset: OpenSecretsDataset) -> list[dict]:
    """Read a CSV file and return rows as dicts keyed by dataset column names.

    The CSV may or may not have a header row.  If the first row matches the
    expected column names (case-insensitive) it is treated as a header and
    skipped.  Otherwise all rows are treated as data.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        raise FileNotFoundError(f"CSV file not found: {filepath}")

    col_names = [c[0] for c in dataset.columns]
    rows: list[dict] = []

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)
        first_row = True
        for raw_row in reader:
            # Trim whitespace from each cell
            row = [cell.strip() for cell in raw_row]

            # Auto-detect header row
            if first_row:
                first_row = False
                if len(row) == len(col_names) and all(
                    r.lower() == c.lower() for r, c in zip(row, col_names)
                ):
                    continue  # skip header

            # Pad or truncate to expected column count
            if len(row) < len(col_names):
                row.extend([""] * (len(col_names) - len(row)))
            elif len(row) > len(col_names):
                row = row[: len(col_names)]

            rows.append(dict(zip(col_names, row)))

    logger.info("Read %d rows from %s", len(rows), filepath)
    return rows


def find_csv_for_dataset(
    data_dir: str | Path, dataset: OpenSecretsDataset
) -> Path | None:
    """Locate the CSV file for a dataset inside *data_dir*.

    Checks for the canonical filename first, then falls back to a
    case-insensitive glob.
    """
    data_dir = Path(data_dir)
    canonical = data_dir / dataset.csv_filename
    if canonical.exists():
        return canonical

    # Fallback: case-insensitive match
    for p in data_dir.iterdir():
        if p.name.lower() == dataset.csv_filename.lower() and p.is_file():
            return p

    return None


# ---------------------------------------------------------------------------
# Downloader (stub — needs API key / researcher access)
# ---------------------------------------------------------------------------

OPENSECRETS_BULK_URL = "https://www.opensecrets.org/open-data/bulk-data"


@dataclass
class DownloadConfig:
    """Configuration for downloading bulk data from OpenSecrets."""

    api_key: str = ""
    base_url: str = OPENSECRETS_BULK_URL
    cycle: int = 2024
    output_dir: str = "./data/opensecrets"
    timeout: int = 120


def download_bulk_data(
    config: DownloadConfig,
    datasets: list[str] | None = None,
) -> dict[str, Path]:
    """Download OpenSecrets bulk data CSVs.

    This is currently a stub — actual bulk downloads require researcher
    access credentials from OpenSecrets.  The function validates the
    configuration and raises an informative error if the API key is missing.

    Args:
        config: Download configuration.
        datasets: List of dataset names to download (default: all).

    Returns:
        Mapping of dataset name -> downloaded file path.

    Raises:
        ValueError: If no API key is configured.
        NotImplementedError: Always, until real download logic is added.
    """
    if not config.api_key:
        api_key_env = os.environ.get("OPENSECRETS_API_KEY", "")
        if api_key_env:
            config.api_key = api_key_env
        else:
            raise ValueError(
                "OpenSecrets API key required. Set OPENSECRETS_API_KEY environment "
                "variable or pass api_key in DownloadConfig."
            )

    target_datasets = datasets or [d.name for d in DATASETS]
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    logger.info(
        "Downloading OpenSecrets bulk data for cycle %d to %s",
        config.cycle,
        out_dir,
    )

    # TODO: Implement actual download logic once we have researcher access.
    # The expected flow is:
    #   1. Authenticate with OpenSecrets API key
    #   2. Request bulk data zip for the given cycle
    #   3. Download and extract CSVs into output_dir
    #   4. Return mapping of dataset name -> file path
    raise NotImplementedError(
        "Bulk download not yet implemented. OpenSecrets researcher access is "
        "required. For now, place CSV files in the data directory and use "
        "the --data-dir flag to load them."
    )
