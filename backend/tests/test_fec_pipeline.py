"""
Tests for the FEC data pipeline — scraper parsing and loader schema generation.

These tests do NOT require a live database or network access. All database
interactions are mocked.
"""

import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.datatalk.pipeline.scrapers.fec import (
    ALL_DATASETS,
    CANDIDATES,
    CANDIDATE_COMMITTEE_LINKAGE,
    COMMITTEES,
    COMMITTEE_CONTRIBUTIONS,
    INDIVIDUAL_CONTRIBUTIONS,
    FECDataset,
    build_url,
    cycle_to_two_digit,
    get_dataset_by_filename,
    load_state,
    parse_file,
    parse_row,
    save_state,
)
from backend.datatalk.pipeline.loaders.fec_loader import (
    COLUMN_TYPES,
    INDEXES,
    PRIMARY_KEYS,
    TABLE_NAMES,
    _coerce_value,
    generate_create_table_sql,
    generate_drop_table_sql,
    generate_full_schema_sql,
    generate_grant_sql,
    generate_index_sql,
)


# ---------------------------------------------------------------------------
# Scraper: URL building
# ---------------------------------------------------------------------------

class TestCycleConversion:
    def test_2024(self):
        assert cycle_to_two_digit(2024) == "24"

    def test_2020(self):
        assert cycle_to_two_digit(2020) == "20"

    def test_2000(self):
        assert cycle_to_two_digit(2000) == "00"

    def test_1998(self):
        assert cycle_to_two_digit(1998) == "98"


class TestBuildUrl:
    def test_candidates_url(self):
        url = build_url(2024, CANDIDATES)
        assert "/24/" in url
        assert "cn.zip" in url
        assert url.startswith("https://")

    def test_individual_contributions_url(self):
        url = build_url(2020, INDIVIDUAL_CONTRIBUTIONS)
        assert "/20/" in url
        assert "itcont.zip" in url

    def test_committee_contributions_url(self):
        url = build_url(2024, COMMITTEE_CONTRIBUTIONS)
        assert "itpas2.zip" in url


# ---------------------------------------------------------------------------
# Scraper: Row parsing
# ---------------------------------------------------------------------------

class TestParseRow:
    def test_valid_candidate_row(self):
        line = "H0AK00097|COX, JOHN ROBERT|REP|2024|AK|H|00|C|N|C00012345|123 MAIN ST||ANCHORAGE|AK|99501"
        row = parse_row(line, CANDIDATES.columns)
        assert row is not None
        assert row["cand_id"] == "H0AK00097"
        assert row["cand_name"] == "COX, JOHN ROBERT"
        assert row["cand_pty_affiliation"] == "REP"
        assert row["cand_election_yr"] == "2024"
        assert row["cand_office"] == "H"
        assert row["cand_office_st"] == "AK"

    def test_trailing_pipe(self):
        """FEC files often have a trailing pipe delimiter."""
        line = "H0AK00097|COX, JOHN ROBERT|REP|2024|AK|H|00|C|N|C00012345|123 MAIN ST||ANCHORAGE|AK|99501|"
        row = parse_row(line, CANDIDATES.columns)
        assert row is not None
        assert row["cand_id"] == "H0AK00097"

    def test_empty_fields(self):
        line = "H0AK00097|COX, JOHN ROBERT|REP|2024|AK|H|00|||C00012345|||ANCHORAGE|AK|99501"
        row = parse_row(line, CANDIDATES.columns)
        assert row is not None
        assert row["cand_ici"] is None
        assert row["cand_status"] is None
        assert row["cand_st1"] is None

    def test_wrong_field_count(self):
        """Rows with wrong number of fields should return None."""
        line = "H0AK00097|COX, JOHN ROBERT|REP"
        row = parse_row(line, CANDIDATES.columns)
        assert row is None

    def test_completely_empty_line(self):
        row = parse_row("", CANDIDATES.columns)
        assert row is None

    def test_committee_row(self):
        line = "C00000059|HALLMARK CARDS PAC|JOHN SMITH|PO BOX 419580||KANSAS CITY|MO|64141|B|Q|REP|M|C|HALLMARK CARDS INC|"
        row = parse_row(line, COMMITTEES.columns)
        assert row is not None
        assert row["cmte_id"] == "C00000059"
        assert row["cmte_nm"] == "HALLMARK CARDS PAC"
        assert row["connected_org_nm"] == "HALLMARK CARDS INC"

    def test_individual_contribution_row(self):
        cols = INDIVIDUAL_CONTRIBUTIONS.columns
        fields = [
            "C00401224", "N", "M2", "P", "202001159187555441",
            "15", "IND", "DOE, JANE", "SEATTLE", "WA", "981011234",
            "ACME CORP", "ENGINEER", "01152020", "500",
            "", "SA11AI.12345", "1234567", "", "", "4011520201234567890",
        ]
        line = "|".join(fields)
        row = parse_row(line, cols)
        assert row is not None
        assert row["cmte_id"] == "C00401224"
        assert row["name"] == "DOE, JANE"
        assert row["transaction_amt"] == "500"
        assert row["sub_id"] == "4011520201234567890"

    def test_newline_stripping(self):
        line = "H0AK00097|COX, JOHN ROBERT|REP|2024|AK|H|00|C|N|C00012345|123 MAIN ST||ANCHORAGE|AK|99501\n"
        row = parse_row(line, CANDIDATES.columns)
        assert row is not None
        assert row["cand_zip"] == "99501"

    def test_whitespace_stripping(self):
        line = "H0AK00097| COX, JOHN ROBERT |REP|2024|AK|H|00|C|N|C00012345|123 MAIN ST||ANCHORAGE|AK|99501"
        row = parse_row(line, CANDIDATES.columns)
        assert row is not None
        assert row["cand_name"] == "COX, JOHN ROBERT"


# ---------------------------------------------------------------------------
# Scraper: File parsing (streaming)
# ---------------------------------------------------------------------------

class TestParseFile:
    def _write_temp_file(self, lines: list[str]) -> Path:
        """Write lines to a temp file and return its path."""
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        for line in lines:
            tmp.write(line + "\n")
        tmp.close()
        return Path(tmp.name)

    def test_basic_parsing(self):
        lines = [
            "H0AK00097|COX, JOHN ROBERT|REP|2024|AK|H|00|C|N|C00012345|123 MAIN ST||ANCHORAGE|AK|99501",
            "S0AL00123|SMITH, ALICE|DEM|2024|AL|S||I|C|C00054321|456 OAK AVE||BIRMINGHAM|AL|35201",
        ]
        filepath = self._write_temp_file(lines)
        try:
            chunks = list(parse_file(filepath, CANDIDATES, chunk_size=100))
            assert len(chunks) == 1
            assert len(chunks[0]) == 2
            assert chunks[0][0]["cand_id"] == "H0AK00097"
            assert chunks[0][1]["cand_id"] == "S0AL00123"
        finally:
            os.unlink(filepath)

    def test_chunking(self):
        lines = [
            f"H0AK{str(i).zfill(5)}|NAME {i}|REP|2024|AK|H|00|C|N|C0000{i}|ST||CITY|AK|99501"
            for i in range(25)
        ]
        filepath = self._write_temp_file(lines)
        try:
            chunks = list(parse_file(filepath, CANDIDATES, chunk_size=10))
            assert len(chunks) == 3
            assert len(chunks[0]) == 10
            assert len(chunks[1]) == 10
            assert len(chunks[2]) == 5
        finally:
            os.unlink(filepath)

    def test_malformed_rows_skipped(self):
        lines = [
            "H0AK00097|COX, JOHN ROBERT|REP|2024|AK|H|00|C|N|C00012345|123 MAIN ST||ANCHORAGE|AK|99501",
            "BAD|ROW|ONLY|THREE|FIELDS",
            "S0AL00123|SMITH, ALICE|DEM|2024|AL|S||I|C|C00054321|456 OAK AVE||BIRMINGHAM|AL|35201",
        ]
        filepath = self._write_temp_file(lines)
        try:
            chunks = list(parse_file(filepath, CANDIDATES, chunk_size=100))
            all_rows = chunks[0]
            assert len(all_rows) == 2  # malformed row skipped
        finally:
            os.unlink(filepath)

    def test_empty_lines_skipped(self):
        lines = [
            "H0AK00097|COX, JOHN ROBERT|REP|2024|AK|H|00|C|N|C00012345|123 MAIN ST||ANCHORAGE|AK|99501",
            "",
            "  ",
            "S0AL00123|SMITH, ALICE|DEM|2024|AL|S||I|C|C00054321|456 OAK AVE||BIRMINGHAM|AL|35201",
        ]
        filepath = self._write_temp_file(lines)
        try:
            chunks = list(parse_file(filepath, CANDIDATES, chunk_size=100))
            assert len(chunks[0]) == 2
        finally:
            os.unlink(filepath)

    def test_empty_file(self):
        filepath = self._write_temp_file([])
        try:
            chunks = list(parse_file(filepath, CANDIDATES, chunk_size=100))
            assert chunks == []
        finally:
            os.unlink(filepath)


# ---------------------------------------------------------------------------
# Scraper: Dataset lookup
# ---------------------------------------------------------------------------

class TestDatasetLookup:
    def test_all_datasets_have_unique_filenames(self):
        filenames = [d.filename for d in ALL_DATASETS]
        assert len(filenames) == len(set(filenames))

    def test_get_dataset_by_filename(self):
        assert get_dataset_by_filename("cn") is CANDIDATES
        assert get_dataset_by_filename("cm") is COMMITTEES
        assert get_dataset_by_filename("itcont") is INDIVIDUAL_CONTRIBUTIONS
        assert get_dataset_by_filename("itpas2") is COMMITTEE_CONTRIBUTIONS
        assert get_dataset_by_filename("ccl") is CANDIDATE_COMMITTEE_LINKAGE

    def test_get_dataset_unknown(self):
        assert get_dataset_by_filename("nonexistent") is None


# ---------------------------------------------------------------------------
# Scraper: State management
# ---------------------------------------------------------------------------

class TestStateManagement:
    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            state = {"2024/cn": {"url": "https://example.com", "downloaded_at": "2024-01-01"}}
            save_state(data_dir, state)

            loaded = load_state(data_dir)
            assert loaded == state

    def test_load_missing_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            data_dir = Path(tmp)
            state = load_state(data_dir)
            assert state == {}


# ---------------------------------------------------------------------------
# Loader: Column definition consistency
# ---------------------------------------------------------------------------

class TestColumnDefinitions:
    def test_all_datasets_have_types(self):
        """Every dataset must have type definitions for all its columns."""
        for ds in ALL_DATASETS:
            assert ds.filename in COLUMN_TYPES, f"Missing types for {ds.filename}"
            for col in ds.columns:
                assert col in COLUMN_TYPES[ds.filename], (
                    f"Missing type for {ds.filename}.{col}"
                )

    def test_all_datasets_have_table_names(self):
        for ds in ALL_DATASETS:
            assert ds.filename in TABLE_NAMES

    def test_all_datasets_have_primary_keys(self):
        for ds in ALL_DATASETS:
            assert ds.filename in PRIMARY_KEYS
            pk_cols = PRIMARY_KEYS[ds.filename]
            for col in pk_cols:
                assert col in ds.columns, (
                    f"PK column {col} not in {ds.filename} columns"
                )

    def test_all_index_columns_exist(self):
        for ds in ALL_DATASETS:
            for idx_name, idx_cols in INDEXES.get(ds.filename, []):
                for col in idx_cols:
                    assert col in ds.columns, (
                        f"Index column {col} (from {idx_name}) not in {ds.filename}"
                    )


# ---------------------------------------------------------------------------
# Loader: SQL generation
# ---------------------------------------------------------------------------

class TestCreateTableSQL:
    def test_candidates_sql(self):
        sql = generate_create_table_sql(CANDIDATES)
        assert 'CREATE TABLE IF NOT EXISTS "fec_candidates"' in sql
        assert '"cand_id" VARCHAR(9) NOT NULL' in sql
        assert '"cand_name" VARCHAR(200)' in sql
        assert "PRIMARY KEY" in sql
        assert '"cand_id"' in sql

    def test_committees_sql(self):
        sql = generate_create_table_sql(COMMITTEES)
        assert '"fec_committees"' in sql
        assert '"cmte_id" VARCHAR(9) NOT NULL' in sql

    def test_individual_contributions_sql(self):
        sql = generate_create_table_sql(INDIVIDUAL_CONTRIBUTIONS)
        assert '"fec_individual_contributions"' in sql
        assert '"transaction_amt" NUMERIC(14,2)' in sql
        assert '"sub_id" BIGINT NOT NULL' in sql

    def test_committee_contributions_sql(self):
        sql = generate_create_table_sql(COMMITTEE_CONTRIBUTIONS)
        assert '"fec_committee_contributions"' in sql
        assert '"cand_id" VARCHAR(9)' in sql

    def test_ccl_sql(self):
        sql = generate_create_table_sql(CANDIDATE_COMMITTEE_LINKAGE)
        assert '"fec_candidate_committee_linkage"' in sql
        assert '"linkage_id" BIGINT NOT NULL' in sql

    def test_all_tables_generate_valid_sql(self):
        """All dataset SQL should be parseable (basic structural check)."""
        for ds in ALL_DATASETS:
            sql = generate_create_table_sql(ds)
            assert sql.startswith("CREATE TABLE")
            assert sql.endswith(");")
            # Check balanced parentheses
            assert sql.count("(") == sql.count(")")


class TestDropTableSQL:
    def test_drop_candidates(self):
        sql = generate_drop_table_sql(CANDIDATES)
        assert sql == 'DROP TABLE IF EXISTS "fec_candidates" CASCADE;'


class TestIndexSQL:
    def test_candidates_indexes(self):
        stmts = generate_index_sql(CANDIDATES)
        assert len(stmts) == len(INDEXES["cn"])
        for stmt in stmts:
            assert stmt.startswith("CREATE INDEX IF NOT EXISTS")
            assert '"fec_candidates"' in stmt

    def test_individual_contributions_indexes(self):
        stmts = generate_index_sql(INDIVIDUAL_CONTRIBUTIONS)
        assert len(stmts) > 0
        # Check that cmte_id index exists
        cmte_idx = [s for s in stmts if "cmte_id" in s]
        assert len(cmte_idx) == 1


class TestGrantSQL:
    def test_grant(self):
        sql = generate_grant_sql(CANDIDATES)
        assert sql == 'GRANT SELECT ON "fec_candidates" TO select_user;'


class TestFullSchemaSQL:
    def test_full_schema_contains_all_tables(self):
        sql = generate_full_schema_sql()
        for ds in ALL_DATASETS:
            table_name = TABLE_NAMES[ds.filename]
            assert f'"{table_name}"' in sql

    def test_full_schema_has_drops_before_creates(self):
        sql = generate_full_schema_sql()
        # Each DROP should appear before the corresponding CREATE
        for ds in ALL_DATASETS:
            table_name = TABLE_NAMES[ds.filename]
            drop_pos = sql.index(f'DROP TABLE IF EXISTS "{table_name}"')
            create_pos = sql.index(f'CREATE TABLE IF NOT EXISTS "{table_name}"')
            assert drop_pos < create_pos, f"DROP should come before CREATE for {table_name}"


# ---------------------------------------------------------------------------
# Loader: Value coercion
# ---------------------------------------------------------------------------

class TestCoerceValue:
    def test_int_coercion(self):
        assert _coerce_value("2024", "SMALLINT") == 2024
        assert _coerce_value("12345678", "BIGINT") == 12345678

    def test_numeric_coercion(self):
        assert _coerce_value("500.00", "NUMERIC(14,2)") == 500.0
        assert _coerce_value("-250.50", "NUMERIC(14,2)") == -250.5

    def test_string_passthrough(self):
        assert _coerce_value("SMITH, JOHN", "VARCHAR(200)") == "SMITH, JOHN"

    def test_none_handling(self):
        assert _coerce_value(None, "VARCHAR(9)") is None
        assert _coerce_value("", "BIGINT") is None
        assert _coerce_value(None, "NUMERIC(14,2)") is None

    def test_invalid_int(self):
        assert _coerce_value("not_a_number", "SMALLINT") is None

    def test_invalid_numeric(self):
        assert _coerce_value("abc", "NUMERIC(14,2)") is None
