"""
OpenSecrets bulk data scraper/downloader.

Defines the expected OpenSecrets data schemas and provides functions to:
- Download bulk data from OpenSecrets (requires registered account)
- Load from local CSV files (for development with sample data)

OpenSecrets bulk data: https://www.opensecrets.org/open-data/bulk-data
Bulk data signup: https://www.opensecrets.org/bulk-data/signup

Authentication: OpenSecrets requires a registered account (email + password)
to download bulk data.  The API was discontinued in April 2025; downloads
are session-based via the website.
"""

import csv
import logging
import os
import zipfile
from dataclasses import dataclass
from pathlib import Path

import httpx

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
    bulk_zip_path: str = ""          # path in bulk download URL, e.g. "CandsCRP/CandsCRP{yy}.zip"
    description: str = ""


# Registry of all datasets we handle
DATASETS: list[OpenSecretsDataset] = [
    OpenSecretsDataset(
        name="candidates",
        table_name="opensecrets_candidates",
        columns=CANDIDATE_COLUMNS,
        csv_filename="cands.csv",
        bulk_zip_path="CandsCRP/CandsCRP{yy}.zip",
        description="Enriched candidate records with party, district, winner status",
    ),
    OpenSecretsDataset(
        name="pacs",
        table_name="opensecrets_pacs",
        columns=PAC_COLUMNS,
        csv_filename="pacs.csv",
        bulk_zip_path="PACs/PACs{yy}.zip",
        description="PAC profiles with industry/ideology classifications",
    ),
    OpenSecretsDataset(
        name="individual_contributions",
        table_name="opensecrets_individual_contributions",
        columns=INDIVIDUAL_CONTRIBUTIONS_COLUMNS,
        csv_filename="indivs.csv",
        bulk_zip_path="Indivs/Indivs{yy}.zip",
        description="Individual contributions with employer/industry coding",
    ),
    OpenSecretsDataset(
        name="pac_to_candidates",
        table_name="opensecrets_pac_to_candidates",
        columns=PAC_TO_CANDIDATE_COLUMNS,
        csv_filename="pac_to_cand.csv",
        bulk_zip_path="PACsToCands/PACsToCands{yy}.zip",
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
# Downloader — session-based auth via opensecrets.org
# ---------------------------------------------------------------------------

OPENSECRETS_BASE = "https://www.opensecrets.org"
OPENSECRETS_LOGIN_URL = f"{OPENSECRETS_BASE}/login"
OPENSECRETS_DOWNLOAD_URL = f"{OPENSECRETS_BASE}/bulk-data/download"

DEFAULT_DATA_DIR = Path(".tmp/opensecrets_data")


@dataclass
class DownloadConfig:
    """Configuration for downloading bulk data from OpenSecrets."""

    email: str = ""
    password: str = ""
    cycle: int = 2024
    output_dir: str | Path = DEFAULT_DATA_DIR
    timeout: int = 300


def _login(client: httpx.Client, email: str, password: str) -> None:
    """Authenticate with OpenSecrets via session cookie.

    Raises ValueError on auth failure.
    """
    # GET login page first (for cookies / CSRF)
    resp = client.get(OPENSECRETS_LOGIN_URL)
    resp.raise_for_status()

    # POST credentials
    resp = client.post(
        OPENSECRETS_LOGIN_URL,
        data={"email": email, "password": password},
        follow_redirects=True,
    )
    resp.raise_for_status()

    # If we're redirected back to login, auth failed
    if "/login" in str(resp.url):
        raise ValueError(
            "OpenSecrets login failed. Check your email/password.\n"
            "Sign up for bulk data access at: https://www.opensecrets.org/bulk-data/signup"
        )

    logger.info("Authenticated with OpenSecrets as %s", email)


def _download_zip(
    client: httpx.Client,
    dataset: OpenSecretsDataset,
    cycle: int,
    out_dir: Path,
) -> Path:
    """Download and extract a single dataset zip file."""
    yy = str(cycle % 100).zfill(2)
    zip_path_param = dataset.bulk_zip_path.format(yy=yy)

    url = f"{OPENSECRETS_DOWNLOAD_URL}?f={zip_path_param}"
    zip_file = out_dir / f"{dataset.name}_{yy}.zip"

    logger.info("Downloading %s from %s", dataset.name, url)
    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        with open(zip_file, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)

    # Extract CSV from zip
    logger.info("Extracting %s", zip_file.name)
    csv_path = out_dir / dataset.csv_filename
    with zipfile.ZipFile(zip_file, "r") as zf:
        members = zf.namelist()
        # Find the data file (txt or csv)
        target = None
        for m in members:
            if m.lower().endswith((".txt", ".csv")):
                target = m
                break
        if target is None:
            raise ValueError(f"No data file found in {zip_file}: {members}")

        zf.extract(target, out_dir)
        extracted = out_dir / target
        if extracted != csv_path:
            extracted.rename(csv_path)

    zip_file.unlink(missing_ok=True)
    logger.info("Extracted %s (%s)", dataset.name, csv_path)
    return csv_path


def download_bulk_data(
    config: DownloadConfig,
    datasets: list[str] | None = None,
) -> dict[str, Path]:
    """Download OpenSecrets bulk data CSVs.

    Authenticates with opensecrets.org using email/password, then downloads
    zip files for the requested cycle.  Credentials can be passed in the
    config or via environment variables OPENSECRETS_EMAIL and
    OPENSECRETS_PASSWORD.

    Returns:
        Mapping of dataset name -> extracted CSV file path.
    """
    email = config.email or os.environ.get("OPENSECRETS_EMAIL", "")
    password = config.password or os.environ.get("OPENSECRETS_PASSWORD", "")

    if not email or not password:
        raise ValueError(
            "OpenSecrets credentials required for bulk downloads.\n"
            "Set OPENSECRETS_EMAIL and OPENSECRETS_PASSWORD environment variables,\n"
            "or pass them in DownloadConfig.\n\n"
            "Sign up at: https://www.opensecrets.org/bulk-data/signup\n\n"
            "Alternatively, download CSV files manually from:\n"
            "  https://www.opensecrets.org/open-data/bulk-data\n"
            "and use --data-dir to load them."
        )

    target_names = set(datasets) if datasets else {d.name for d in DATASETS}
    out_dir = Path(config.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    results: dict[str, Path] = {}

    with httpx.Client(timeout=config.timeout, follow_redirects=True) as client:
        _login(client, email, password)

        for ds in DATASETS:
            if ds.name not in target_names:
                continue
            if not ds.bulk_zip_path:
                logger.warning("No download path for %s, skipping", ds.name)
                continue
            csv_path = _download_zip(client, ds, config.cycle, out_dir)
            results[ds.name] = csv_path

    return results
