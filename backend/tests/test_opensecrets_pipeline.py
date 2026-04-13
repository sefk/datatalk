"""
Tests for the OpenSecrets data pipeline.

Covers CSV parsing, schema DDL generation, data coercion, and end-to-end
loading with a mocked database connection.
"""

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# Scraper tests (CSV parsing, schema definitions)
# ---------------------------------------------------------------------------

from backend.datatalk.pipeline.scrapers.opensecrets import (
    CANDIDATE_COLUMNS,
    DATASETS,
    DATASETS_BY_NAME,
    DownloadConfig,
    download_bulk_data,
    find_csv_for_dataset,
    read_csv,
)

SAMPLE_DATA_DIR = Path(__file__).resolve().parent.parent / "datatalk" / "pipeline" / "sample_data" / "opensecrets"


class TestSchemaDefinitions:
    """Verify the dataset registry is well-formed."""

    def test_all_datasets_registered(self):
        assert len(DATASETS) == 4
        names = {d.name for d in DATASETS}
        assert names == {"candidates", "pacs", "individual_contributions", "pac_to_candidates"}

    def test_datasets_have_unique_table_names(self):
        table_names = [d.table_name for d in DATASETS]
        assert len(table_names) == len(set(table_names))

    def test_all_table_names_have_opensecrets_prefix(self):
        for ds in DATASETS:
            assert ds.table_name.startswith("opensecrets_"), (
                f"{ds.name} table '{ds.table_name}' missing opensecrets_ prefix"
            )

    def test_each_dataset_has_columns(self):
        for ds in DATASETS:
            assert len(ds.columns) > 0, f"{ds.name} has no columns"

    def test_each_dataset_has_csv_filename(self):
        for ds in DATASETS:
            assert ds.csv_filename, f"{ds.name} has no csv_filename"

    def test_by_name_lookup(self):
        assert DATASETS_BY_NAME["candidates"].table_name == "opensecrets_candidates"
        assert DATASETS_BY_NAME["pacs"].table_name == "opensecrets_pacs"

    def test_candidate_columns_include_key_fields(self):
        col_names = [c[0] for c in CANDIDATE_COLUMNS]
        assert "CID" in col_names
        assert "Cycle" in col_names
        assert "FirstLastP" in col_names
        assert "Party" in col_names


class TestCSVParsing:
    """Test CSV reading against the sample data files."""

    def test_read_candidates_csv(self):
        ds = DATASETS_BY_NAME["candidates"]
        rows = read_csv(SAMPLE_DATA_DIR / "cands.csv", ds)
        assert len(rows) == 15  # 15 data rows
        # Check first row
        assert rows[0]["CID"] == "N00012345"
        assert rows[0]["FirstLastP"] == "Warren, Elizabeth"
        assert rows[0]["Party"] == "D"
        assert rows[0]["Cycle"] == "2024"

    def test_read_pacs_csv(self):
        ds = DATASETS_BY_NAME["pacs"]
        rows = read_csv(SAMPLE_DATA_DIR / "pacs.csv", ds)
        assert len(rows) == 12
        assert rows[0]["PACShort"] == "ActBlue"

    def test_read_indivs_csv(self):
        ds = DATASETS_BY_NAME["individual_contributions"]
        rows = read_csv(SAMPLE_DATA_DIR / "indivs.csv", ds)
        assert len(rows) == 15
        assert rows[0]["Contrib"] == "Doe, Jane"
        assert rows[0]["Amount"] == "2500"

    def test_read_pac_to_cand_csv(self):
        ds = DATASETS_BY_NAME["pac_to_candidates"]
        rows = read_csv(SAMPLE_DATA_DIR / "pac_to_cand.csv", ds)
        assert len(rows) == 15
        assert rows[0]["PACID"] == "C00012345"

    def test_read_csv_missing_file(self):
        ds = DATASETS_BY_NAME["candidates"]
        with pytest.raises(FileNotFoundError):
            read_csv("/nonexistent/path.csv", ds)

    def test_read_csv_without_header(self, tmp_path):
        """CSV with no header row — all rows are data."""
        ds = DATASETS_BY_NAME["candidates"]
        csv_content = '2024,H4XX00001,N00099999,"Test, Person",D,CA01,CA01,Y,Y,I,DW,N\n'
        csv_file = tmp_path / "cands.csv"
        csv_file.write_text(csv_content)
        rows = read_csv(csv_file, ds)
        assert len(rows) == 1
        assert rows[0]["FirstLastP"] == "Test, Person"

    def test_read_csv_short_row_is_padded(self, tmp_path):
        """A row with fewer columns than expected gets padded."""
        ds = DATASETS_BY_NAME["candidates"]
        csv_content = 'Cycle,FECCandID,CID,FirstLastP,Party,DistIDRunFor,DistIDCurr,CurrCand,CycleCand,CRPICO,RecipCode,NoPacs\n'
        csv_content += '2024,H4XX00001,N00099999,"Short, Row"\n'
        csv_file = tmp_path / "cands.csv"
        csv_file.write_text(csv_content)
        rows = read_csv(csv_file, ds)
        assert len(rows) == 1
        assert rows[0]["Party"] == ""


class TestFindCSV:
    """Test CSV file discovery."""

    def test_find_canonical_filename(self):
        ds = DATASETS_BY_NAME["candidates"]
        result = find_csv_for_dataset(SAMPLE_DATA_DIR, ds)
        assert result is not None
        assert result.name == "cands.csv"

    def test_find_returns_none_for_missing(self, tmp_path):
        ds = DATASETS_BY_NAME["candidates"]
        result = find_csv_for_dataset(tmp_path, ds)
        assert result is None


class TestDownloader:
    """Test the download stub."""

    def test_download_raises_without_api_key(self):
        config = DownloadConfig(api_key="", cycle=2024)
        with pytest.raises(ValueError, match="API key"):
            download_bulk_data(config)

    def test_download_raises_not_implemented(self):
        config = DownloadConfig(api_key="test-key-123", cycle=2024)
        with pytest.raises(NotImplementedError, match="not yet implemented"):
            download_bulk_data(config)


# ---------------------------------------------------------------------------
# Loader tests (DDL generation, data coercion, batch loading)
# ---------------------------------------------------------------------------

from backend.datatalk.pipeline.loaders.opensecrets_loader import (
    IMPORT_LOG_TABLE,
    INDEXES,
    _coerce_value,
    _prepare_row,
    create_indexes,
    create_schema,
    create_table_ddl,
    load_dataset,
    load_from_csv,
)


class TestDDLGeneration:
    """Test SQL DDL generation."""

    def test_create_table_ddl_candidates(self):
        ds = DATASETS_BY_NAME["candidates"]
        ddl = create_table_ddl(ds)
        assert "CREATE TABLE IF NOT EXISTS opensecrets_candidates" in ddl
        assert "cycle SMALLINT" in ddl
        assert "cid VARCHAR(9)" in ddl
        assert "firstlastp VARCHAR(50)" in ddl

    def test_create_table_ddl_all_datasets(self):
        for ds in DATASETS:
            ddl = create_table_ddl(ds)
            assert f"CREATE TABLE IF NOT EXISTS {ds.table_name}" in ddl
            # Every column should appear (lowercased)
            for col_name, col_type in ds.columns:
                assert col_name.lower() in ddl


class TestValueCoercion:
    """Test type coercion from CSV strings to Python/Postgres types."""

    def test_empty_string_to_none(self):
        assert _coerce_value("", "VARCHAR(50)") is None

    def test_none_to_none(self):
        assert _coerce_value(None, "INTEGER") is None

    def test_int_coercion(self):
        assert _coerce_value("2024", "SMALLINT") == 2024
        assert _coerce_value("5000", "INTEGER") == 5000

    def test_int_invalid_returns_none(self):
        assert _coerce_value("not-a-number", "INTEGER") is None

    def test_date_coercion_slash_format(self):
        result = _coerce_value("01/15/2024", "DATE")
        assert result == date(2024, 1, 15)

    def test_date_coercion_iso_format(self):
        result = _coerce_value("2024-01-15", "DATE")
        assert result == date(2024, 1, 15)

    def test_date_invalid_returns_none(self):
        assert _coerce_value("not-a-date", "DATE") is None

    def test_varchar_passthrough(self):
        assert _coerce_value("hello", "VARCHAR(50)") == "hello"

    def test_prepare_row(self):
        ds = DATASETS_BY_NAME["pac_to_candidates"]
        row = {
            "Cycle": "2024",
            "FECRecNo": "FEC-001",
            "PACID": "C00012345",
            "CID": "N00012345",
            "Amount": "5000",
            "Date": "01/20/2024",
            "RealCode": "J2100",
            "Type": "24K",
            "DI": "D",
            "FECCandID": "H4CA12345",
        }
        result = _prepare_row(row, ds)
        assert result[0] == 2024           # Cycle -> int
        assert result[4] == 5000           # Amount -> int
        assert result[5] == date(2024, 1, 20)  # Date -> date


class TestCreateSchema:
    """Test schema creation with a mock connection."""

    def test_create_schema_executes_ddl(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_schema(mock_conn)

        # Should execute DDL for import log + 4 dataset tables = 5 statements
        assert mock_cur.execute.call_count == 5
        mock_conn.commit.assert_called_once()

    def test_create_indexes_executes_ddl(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)

        create_indexes(mock_conn)

        assert mock_cur.execute.call_count == len(INDEXES)
        mock_conn.commit.assert_called_once()


class TestLoadDataset:
    """Test data loading with a mock connection."""

    def _make_mock_conn(self):
        """Create a mock connection with working cursor context manager."""
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        # Make the import log INSERT return an id
        mock_cur.fetchone.return_value = (1,)
        return mock_conn, mock_cur

    @patch("backend.datatalk.pipeline.loaders.opensecrets_loader.psycopg2.extras.execute_batch")
    def test_load_dataset_inserts_rows(self, mock_execute_batch):
        mock_conn, mock_cur = self._make_mock_conn()
        ds = DATASETS_BY_NAME["candidates"]

        rows = [
            {"Cycle": "2024", "FECCandID": "H4XX00001", "CID": "N00099999",
             "FirstLastP": "Test, Person", "Party": "D", "DistIDRunFor": "CA01",
             "DistIDCurr": "CA01", "CurrCand": "Y", "CycleCand": "Y",
             "CRPICO": "I", "RecipCode": "DW", "NoPacs": "N"},
        ]

        n = load_dataset(mock_conn, ds, rows, truncate_first=True)
        assert n == 1
        # execute_batch should have been called once with 1 row
        mock_execute_batch.assert_called_once()

    @patch("backend.datatalk.pipeline.loaders.opensecrets_loader.psycopg2.extras.execute_batch")
    def test_load_with_cycle_deletes_by_cycle(self, mock_execute_batch):
        mock_conn, mock_cur = self._make_mock_conn()
        ds = DATASETS_BY_NAME["candidates"]

        n = load_dataset(mock_conn, ds, [], cycle=2024, truncate_first=True)
        assert n == 0
        # Should have executed a DELETE WHERE cycle = 2024
        delete_calls = [
            c for c in mock_cur.execute.call_args_list
            if "DELETE" in str(c)
        ]
        assert len(delete_calls) == 1

    @patch("backend.datatalk.pipeline.loaders.opensecrets_loader.psycopg2.extras.execute_batch")
    def test_load_without_truncate_skips_delete(self, mock_execute_batch):
        mock_conn, mock_cur = self._make_mock_conn()
        ds = DATASETS_BY_NAME["candidates"]

        n = load_dataset(mock_conn, ds, [], truncate_first=False)
        assert n == 0
        # Should NOT have a DELETE or TRUNCATE
        for c in mock_cur.execute.call_args_list:
            sql = str(c)
            assert "DELETE" not in sql
            assert "TRUNCATE" not in sql


class TestLoadFromCSV:
    """Test end-to-end CSV loading with a mock connection."""

    def _make_mock_conn(self):
        mock_conn = MagicMock()
        mock_cur = MagicMock()
        mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cur)
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_cur.fetchone.return_value = (1,)
        return mock_conn, mock_cur

    @patch("backend.datatalk.pipeline.loaders.opensecrets_loader.psycopg2.extras.execute_batch")
    def test_load_from_csv_all_datasets(self, mock_execute_batch):
        mock_conn, mock_cur = self._make_mock_conn()

        results = load_from_csv(mock_conn, SAMPLE_DATA_DIR)

        assert "candidates" in results
        assert "pacs" in results
        assert "individual_contributions" in results
        assert "pac_to_candidates" in results
        assert results["candidates"] == 15
        assert results["pacs"] == 12
        assert results["individual_contributions"] == 15
        assert results["pac_to_candidates"] == 15

    @patch("backend.datatalk.pipeline.loaders.opensecrets_loader.psycopg2.extras.execute_batch")
    def test_load_from_csv_specific_dataset(self, mock_execute_batch):
        mock_conn, mock_cur = self._make_mock_conn()

        results = load_from_csv(
            mock_conn, SAMPLE_DATA_DIR, dataset_names=["candidates"]
        )
        assert "candidates" in results
        assert "pacs" not in results

    def test_load_from_csv_missing_dir(self):
        mock_conn = MagicMock()
        with pytest.raises(FileNotFoundError):
            load_from_csv(mock_conn, "/nonexistent/dir")

    @patch("backend.datatalk.pipeline.loaders.opensecrets_loader.psycopg2.extras.execute_batch")
    def test_load_from_csv_unknown_dataset_skipped(self, mock_execute_batch):
        mock_conn, mock_cur = self._make_mock_conn()

        results = load_from_csv(
            mock_conn, SAMPLE_DATA_DIR, dataset_names=["nonexistent"]
        )
        assert len(results) == 0


class TestTableNameConventions:
    """Verify OpenSecrets tables won't conflict with FEC tables."""

    def test_no_table_starts_with_fec(self):
        for ds in DATASETS:
            assert not ds.table_name.startswith("fec_"), (
                f"OpenSecrets table {ds.table_name} conflicts with FEC namespace"
            )

    def test_import_log_has_prefix(self):
        assert IMPORT_LOG_TABLE.startswith("opensecrets_")

    def test_all_indexes_reference_opensecrets_tables(self):
        for idx_name, table_name, _ in INDEXES:
            assert table_name.startswith("opensecrets_"), (
                f"Index {idx_name} references non-OpenSecrets table {table_name}"
            )
