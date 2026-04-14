"""
DIME (Database on Ideology, Money in Politics, and Elections) scraper.

Downloads and parses DIME data from Harvard Dataverse (public, no auth required).
DIME is maintained by Adam Bonica at Stanford and covers 850M+ itemized political
contributions from 1979-2014 (public v3) with ideology scores (CFscores).

Data sources:
  - Harvard Dataverse: https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/O5PX0B
  - Stanford (v4.0, 1979-2024): https://data.stanford.edu/dime (manual download)

Supported datasets:
  - Recipients: candidates and committees with CFscores
  - Contributions: itemized contribution records by election cycle
  - Donors: aggregated contributor records with CFscores
"""

import csv
import gzip
import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Generator, Optional

import httpx

logger = logging.getLogger(__name__)

DEFAULT_DATA_DIR = Path(".tmp/dime_data")

DATAVERSE_API = "https://dataverse.harvard.edu/api"
DIME_DOI = "doi:10.7910/DVN/O5PX0B"


# ---------------------------------------------------------------------------
# Column definitions — from DIME codebook
# Column names in the CSV use dot notation (e.g. "bonica.rid"); we convert
# to underscores for Postgres column names.
# ---------------------------------------------------------------------------

# Postgres TEXT and VARCHAR have identical performance; TEXT avoids truncation
# errors from unpredictable external data widths.

RECIPIENT_COLUMNS = [
    ("cycle", "SMALLINT"),
    ("fecyear", "SMALLINT"),
    ("Cand.ID", "TEXT"),
    ("FEC.ID", "TEXT"),                # FEC candidate ID — linkage to FEC data
    ("bonica.rid", "TEXT"),
    ("bonica.cid", "TEXT"),
    ("name", "TEXT"),
    ("lname", "TEXT"),
    ("fname", "TEXT"),
    ("party", "INTEGER"),              # 100=Dem, 200=Rep, 328=Ind, up to 90000
    ("state", "TEXT"),
    ("seat", "TEXT"),                   # e.g. federal:house, federal:senate
    ("district", "TEXT"),
    ("Incum.Chall", "TEXT"),           # I/C/O
    ("recipient.cfscore", "REAL"),     # CFscore ideology (-2 to +2, neg=liberal)
    ("contributor.cfscore", "REAL"),   # when recipient is also a donor
    ("recipient.cfscore.dyn", "REAL"),
    ("cand.gender", "TEXT"),
    ("recipient.type", "TEXT"),
    ("igcat", "TEXT"),                  # interest group category
    ("comtype", "TEXT"),               # committee type
    ("num.givers", "INTEGER"),
    ("num.givers.total", "INTEGER"),
    ("total.receipts", "NUMERIC(14,2)"),
    ("total.indiv.contrib", "NUMERIC(14,2)"),
    ("total.pac.contribs", "NUMERIC(14,2)"),
    ("ran.primary", "SMALLINT"),
    ("ran.general", "SMALLINT"),
    ("winner", "SMALLINT"),
    ("gen.elect.pct", "REAL"),
]

CONTRIBUTION_COLUMNS = [
    ("cycle", "SMALLINT"),
    ("transaction.id", "TEXT"),
    ("transaction.type", "TEXT"),
    ("amount", "NUMERIC(14,2)"),
    ("date", "DATE"),
    ("bonica.cid", "TEXT"),            # contributor ID
    ("contributor.name", "TEXT"),
    ("contributor.type", "TEXT"),       # I=Individual, C=Committee
    ("contributor.gender", "TEXT"),
    ("contributor.state", "TEXT"),
    ("contributor.zipcode", "TEXT"),
    ("contributor.occupation", "TEXT"),
    ("contributor.employer", "TEXT"),
    ("is.corp", "SMALLINT"),
    ("recipient.name", "TEXT"),
    ("bonica.rid", "TEXT"),            # recipient ID
    ("recipient.party", "INTEGER"),
    ("recipient.type", "TEXT"),
    ("recipient.state", "TEXT"),
    ("seat", "TEXT"),
    ("election.type", "TEXT"),         # P=Primary, G=General
    ("contributor.cfscore", "REAL"),
    ("candidate.cfscore", "REAL"),     # recipient ideology score
]

DONOR_COLUMNS = [
    ("bonica.cid", "TEXT"),
    ("contributor.type", "TEXT"),
    ("num.records", "INTEGER"),
    ("num.distinct", "INTEGER"),
    ("most.recent.contributor.name", "TEXT"),
    ("most.recent.contributor.city", "TEXT"),
    ("most.recent.contributor.zipcode", "TEXT"),
    ("most.recent.contributor.state", "TEXT"),
    ("most.recent.contributor.occupation", "TEXT"),
    ("most.recent.contributor.employer", "TEXT"),
    ("contributor.gender", "TEXT"),
    ("is_corp", "SMALLINT"),
    ("contributor.cfscore", "REAL"),
    ("first_cycle_active", "SMALLINT"),
    ("last_cycle_active", "SMALLINT"),
]


@dataclass
class DIMEDataset:
    """Metadata for one DIME data file."""

    name: str                        # e.g. "recipients", "contributions_2012"
    table_name: str                  # Postgres table name
    columns: list                    # list of (csv_col_name, pg_type) tuples
    dataverse_file_ids: list[int] = field(default_factory=list)  # Harvard Dataverse file IDs
    filenames: list[str] = field(default_factory=list)  # expected filenames
    description: str = ""


# Harvard Dataverse file IDs (from API query, DIME v3 public release)
RECIPIENTS = DIMEDataset(
    name="recipients",
    table_name="dime_recipients",
    columns=RECIPIENT_COLUMNS,
    dataverse_file_ids=[2865310],
    filenames=["dime_recipients_all_1979_2014.csv.gz"],
    description="Candidates and committees with CFscore ideology estimates",
)

# Contribution files are split by election cycle year
_CONTRIB_FILES = {
    1980: 2865280, 1982: 2865279, 1984: 2865282, 1986: 2865281,
    1988: 2865283, 1990: 2865284, 1992: 2865285, 1994: 2865286,
    1996: 2865287, 1998: 2865288, 2000: 2865289, 2002: 2865290,
    2004: 2865291, 2006: 2865292, 2008: 2865295, 2010: 2865296,
    2012: 2865298, 2014: 2865299,
}
# Office-specific contribution files
_CONTRIB_OFFICE_FILES = {
    "governor": 2865297,
    "judicial": 2865293,
    "president": 2865294,
}

CONTRIBUTIONS = DIMEDataset(
    name="contributions",
    table_name="dime_contributions",
    columns=CONTRIBUTION_COLUMNS,
    dataverse_file_ids=list(_CONTRIB_FILES.values()),
    filenames=[f"contribDB_{yr}.csv.gz" for yr in _CONTRIB_FILES],
    description="Itemized contribution records with ideology scores",
)

DONORS = DIMEDataset(
    name="donors",
    table_name="dime_donors",
    columns=DONOR_COLUMNS,
    dataverse_file_ids=[2865300],
    filenames=["dime_contributors_1979_2014.csv.gz"],
    description="Aggregated contributor records with CFscores",
)

ALL_DATASETS = [RECIPIENTS, CONTRIBUTIONS, DONORS]
DATASETS_BY_NAME: dict[str, DIMEDataset] = {d.name: d for d in ALL_DATASETS}


def get_contrib_cycles() -> list[int]:
    """Return available contribution file cycles."""
    return sorted(_CONTRIB_FILES.keys())


# ---------------------------------------------------------------------------
# Download from Harvard Dataverse
# ---------------------------------------------------------------------------

def _download_dataverse_file(
    client: httpx.Client,
    file_id: int,
    out_path: Path,
) -> None:
    """Download a single file from Harvard Dataverse by file ID."""
    url = f"{DATAVERSE_API}/access/datafile/{file_id}"
    logger.info("Downloading Dataverse file %d -> %s", file_id, out_path.name)

    with client.stream("GET", url) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length", 0))
        downloaded = 0
        with open(out_path, "wb") as f:
            for chunk in resp.iter_bytes(chunk_size=65536):
                f.write(chunk)
                downloaded += len(chunk)
                if total > 0 and downloaded % (10 * 1024 * 1024) < 65536:
                    pct = downloaded * 100 // total
                    logger.info("  %s: %d MB / %d MB (%d%%)",
                                out_path.name, downloaded // (1024*1024),
                                total // (1024*1024), pct)

    logger.info("Downloaded %s (%.1f MB)", out_path.name, out_path.stat().st_size / (1024*1024))


def download_recipients(
    data_dir: Path = DEFAULT_DATA_DIR,
    force: bool = False,
    client: Optional[httpx.Client] = None,
) -> Path:
    """Download the DIME recipients file."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    out_path = data_dir / RECIPIENTS.filenames[0]
    if out_path.exists() and not force:
        logger.info("Skipping %s — already exists", out_path.name)
        return out_path

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=600, follow_redirects=True)
    try:
        _download_dataverse_file(client, RECIPIENTS.dataverse_file_ids[0], out_path)
    finally:
        if own_client:
            client.close()

    return out_path


def download_contributions(
    data_dir: Path = DEFAULT_DATA_DIR,
    cycles: list[int] | None = None,
    force: bool = False,
    client: Optional[httpx.Client] = None,
) -> dict[int, Path]:
    """Download contribution files for specified cycles.

    If cycles is None, downloads all available cycles.
    Returns mapping of cycle -> file path.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    if cycles is None:
        cycles = get_contrib_cycles()

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=600, follow_redirects=True)

    results: dict[int, Path] = {}
    try:
        for cycle in cycles:
            file_id = _CONTRIB_FILES.get(cycle)
            if file_id is None:
                logger.warning("No DIME contribution file for cycle %d, skipping", cycle)
                continue
            filename = f"contribDB_{cycle}.csv.gz"
            out_path = data_dir / filename
            if out_path.exists() and not force:
                logger.info("Skipping %s — already exists", filename)
                results[cycle] = out_path
                continue
            _download_dataverse_file(client, file_id, out_path)
            results[cycle] = out_path
    finally:
        if own_client:
            client.close()

    return results


def download_donors(
    data_dir: Path = DEFAULT_DATA_DIR,
    force: bool = False,
    client: Optional[httpx.Client] = None,
) -> Path:
    """Download the DIME donors/contributors file."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)

    out_path = data_dir / DONORS.filenames[0]
    if out_path.exists() and not force:
        logger.info("Skipping %s — already exists", out_path.name)
        return out_path

    own_client = client is None
    if own_client:
        client = httpx.Client(timeout=600, follow_redirects=True)
    try:
        _download_dataverse_file(client, DONORS.dataverse_file_ids[0], out_path)
    finally:
        if own_client:
            client.close()

    return out_path


def download_all(
    data_dir: Path = DEFAULT_DATA_DIR,
    cycles: list[int] | None = None,
    datasets: list[str] | None = None,
    force: bool = False,
) -> dict[str, list[Path]]:
    """Download all (or selected) DIME datasets.

    Args:
        data_dir: Directory to save files.
        cycles: Contribution cycles to download (default: all).
        datasets: Dataset names to download (default: all).
        force: Re-download even if files exist.

    Returns:
        Mapping of dataset name -> list of downloaded file paths.
    """
    target_names = set(datasets) if datasets else {"recipients", "contributions", "donors"}
    data_dir = Path(data_dir)
    results: dict[str, list[Path]] = {}

    with httpx.Client(timeout=600, follow_redirects=True) as client:
        if "recipients" in target_names:
            path = download_recipients(data_dir, force=force, client=client)
            results["recipients"] = [path]

        if "contributions" in target_names:
            paths = download_contributions(data_dir, cycles=cycles, force=force, client=client)
            results["contributions"] = list(paths.values())

        if "donors" in target_names:
            path = download_donors(data_dir, force=force, client=client)
            results["donors"] = [path]

    return results


# ---------------------------------------------------------------------------
# Parsing — streaming CSV.GZ files
# ---------------------------------------------------------------------------

def _normalize_col_name(name: str) -> str:
    """Convert DIME dot-notation column names to Postgres-safe underscores."""
    return name.strip().replace(".", "_").replace(" ", "_").lower()


def parse_gz_csv(
    filepath: Path,
    dataset: DIMEDataset,
    chunk_size: int = 10000,
) -> Generator[list[dict], None, None]:
    """Stream-parse a gzipped CSV file in chunks.

    DIME CSVs have header rows.  We read the header to map columns, then
    yield chunks of rows as dicts keyed by the normalized column name.
    Only columns defined in the dataset schema are included.

    Yields lists of dicts, up to chunk_size rows per yield.
    """
    filepath = Path(filepath)
    expected_cols = {_normalize_col_name(c[0]) for c in dataset.columns}

    opener = gzip.open if filepath.name.endswith(".gz") else open
    with opener(filepath, "rt", encoding="utf-8", errors="replace") as f:
        reader = csv.reader(f)

        # Read and map header
        header = next(reader)
        header_normalized = [_normalize_col_name(h) for h in header]

        # Build index: which CSV columns map to our schema columns
        col_indices: list[tuple[int, str]] = []
        for i, norm_name in enumerate(header_normalized):
            if norm_name in expected_cols:
                col_indices.append((i, norm_name))

        found_cols = {name for _, name in col_indices}
        missing = expected_cols - found_cols
        if missing:
            logger.warning(
                "Columns defined in schema but missing from CSV %s: %s",
                filepath.name, ", ".join(sorted(missing)),
            )

        chunk: list[dict] = []
        malformed = 0

        for row in reader:
            if not row or (len(row) == 1 and not row[0].strip()):
                continue

            record = {}
            for idx, col_name in col_indices:
                if idx < len(row):
                    val = row[idx].strip()
                    record[col_name] = val if val else None
                else:
                    record[col_name] = None

            chunk.append(record)
            if len(chunk) >= chunk_size:
                yield chunk
                chunk = []

        if chunk:
            yield chunk

        if malformed > 0:
            logger.warning("Malformed rows in %s: %d", filepath.name, malformed)


def find_data_files(
    data_dir: Path,
    dataset: DIMEDataset,
) -> list[Path]:
    """Find data files for a dataset in the given directory.

    Looks for known filenames, then falls back to pattern matching.
    """
    data_dir = Path(data_dir)
    found = []

    for fname in dataset.filenames:
        path = data_dir / fname
        if path.exists():
            found.append(path)

    if found:
        return sorted(found)

    # Fallback: glob for contribution files by pattern
    if dataset.name == "contributions":
        for p in sorted(data_dir.glob("contribDB_*.csv*")):
            found.append(p)
    elif dataset.name == "recipients":
        for p in sorted(data_dir.glob("dime_recipients*.csv*")):
            found.append(p)
    elif dataset.name == "donors":
        for p in sorted(data_dir.glob("dime_contributors*.csv*")):
            found.append(p)

    return found
