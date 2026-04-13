"""Tests for the benchmark runner.

Validates question schema, loading logic, filtering, and result structure.
No actual agent calls are made.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
import yaml

# Import from the runner script
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from run_benchmark import (
    ALL_FIELDS,
    REQUIRED_FIELDS,
    VALID_CATEGORIES,
    BenchmarkQuestion,
    BenchmarkResult,
    filter_questions,
    load_questions,
    run_benchmark,
    write_results,
)

QUESTIONS_FILE = Path(__file__).resolve().parent / "benchmarks" / "questions.yaml"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def questions():
    """Load the real questions file."""
    return load_questions(QUESTIONS_FILE)


def _write_yaml(tmp_path: Path, data: dict) -> Path:
    """Helper to write a YAML file and return its path."""
    path = tmp_path / "questions.yaml"
    with open(path, "w") as f:
        yaml.dump(data, f)
    return path


# ---------------------------------------------------------------------------
# Schema validation tests
# ---------------------------------------------------------------------------


class TestQuestionSchema:
    """Validate the structure of the questions YAML file."""

    def test_questions_file_exists(self):
        assert QUESTIONS_FILE.exists(), f"Questions file missing: {QUESTIONS_FILE}"

    def test_loads_successfully(self, questions):
        assert len(questions) > 0

    def test_minimum_question_count(self, questions):
        """The seed set should have at least 10 questions."""
        assert len(questions) >= 10

    def test_all_ids_unique(self, questions):
        ids = [q.id for q in questions]
        assert len(ids) == len(set(ids)), f"Duplicate IDs found: {ids}"

    def test_all_categories_valid(self, questions):
        for q in questions:
            assert q.category in VALID_CATEGORIES, (
                f"Question '{q.id}' has invalid category '{q.category}'"
            )

    def test_all_required_fields_present(self, questions):
        for q in questions:
            assert q.id, f"Question missing id"
            assert q.question, f"Question '{q.id}' missing question text"
            assert q.category, f"Question '{q.id}' missing category"

    def test_each_category_has_questions(self, questions):
        """Every valid category should have at least one question."""
        categories_present = {q.category for q in questions}
        for cat in VALID_CATEGORIES:
            assert cat in categories_present, (
                f"No questions found for category '{cat}'"
            )

    def test_question_text_nonempty(self, questions):
        for q in questions:
            assert len(q.question.strip()) > 0, (
                f"Question '{q.id}' has empty question text"
            )

    def test_known_failures_category_has_multiple(self, questions):
        """V1 failure cases should have at least 2 questions for meaningful eval."""
        kf = [q for q in questions if q.category == "known_failures"]
        assert len(kf) >= 2


# ---------------------------------------------------------------------------
# Loading and validation tests
# ---------------------------------------------------------------------------


class TestLoadQuestions:
    """Test question loading and error handling."""

    def test_missing_file_raises(self, tmp_path):
        with pytest.raises(FileNotFoundError):
            load_questions(tmp_path / "nonexistent.yaml")

    def test_missing_questions_key(self, tmp_path):
        path = _write_yaml(tmp_path, {"other_key": []})
        with pytest.raises(ValueError, match="top-level 'questions' key"):
            load_questions(path)

    def test_empty_questions_list(self, tmp_path):
        path = _write_yaml(tmp_path, {"questions": []})
        with pytest.raises(ValueError, match="non-empty list"):
            load_questions(path)

    def test_missing_required_field(self, tmp_path):
        path = _write_yaml(
            tmp_path,
            {"questions": [{"id": "test-01", "question": "Hello?"}]},
        )
        with pytest.raises(ValueError, match="missing required fields"):
            load_questions(path)

    def test_invalid_category(self, tmp_path):
        path = _write_yaml(
            tmp_path,
            {
                "questions": [
                    {
                        "id": "test-01",
                        "question": "Hello?",
                        "category": "invalid_cat",
                    }
                ]
            },
        )
        with pytest.raises(ValueError, match="invalid category"):
            load_questions(path)

    def test_duplicate_ids(self, tmp_path):
        path = _write_yaml(
            tmp_path,
            {
                "questions": [
                    {"id": "dup-01", "question": "Q1?", "category": "jargon"},
                    {"id": "dup-01", "question": "Q2?", "category": "jargon"},
                ]
            },
        )
        with pytest.raises(ValueError, match="Duplicate question ID"):
            load_questions(path)

    def test_unknown_fields_rejected(self, tmp_path):
        path = _write_yaml(
            tmp_path,
            {
                "questions": [
                    {
                        "id": "test-01",
                        "question": "Hello?",
                        "category": "jargon",
                        "bogus_field": "oops",
                    }
                ]
            },
        )
        with pytest.raises(ValueError, match="unknown fields"):
            load_questions(path)

    def test_valid_minimal_question(self, tmp_path):
        path = _write_yaml(
            tmp_path,
            {
                "questions": [
                    {"id": "min-01", "question": "Test?", "category": "jargon"}
                ]
            },
        )
        qs = load_questions(path)
        assert len(qs) == 1
        assert qs[0].id == "min-01"
        assert qs[0].expected_answer is None
        assert qs[0].notes is None

    def test_valid_full_question(self, tmp_path):
        path = _write_yaml(
            tmp_path,
            {
                "questions": [
                    {
                        "id": "full-01",
                        "question": "Test?",
                        "category": "basic_working",
                        "expected_answer": "42",
                        "notes": "A test question",
                    }
                ]
            },
        )
        qs = load_questions(path)
        assert qs[0].expected_answer == "42"
        assert qs[0].notes == "A test question"


# ---------------------------------------------------------------------------
# Filtering tests
# ---------------------------------------------------------------------------


class TestFilterQuestions:
    def test_filter_single_category(self, questions):
        filtered = filter_questions(questions, ["jargon"])
        assert all(q.category == "jargon" for q in filtered)
        assert len(filtered) > 0

    def test_filter_multiple_categories(self, questions):
        filtered = filter_questions(questions, ["jargon", "edge_cases"])
        assert all(q.category in {"jargon", "edge_cases"} for q in filtered)

    def test_filter_invalid_category_raises(self, questions):
        with pytest.raises(ValueError, match="Invalid filter categories"):
            filter_questions(questions, ["nonexistent"])

    def test_filter_empty_result(self, tmp_path):
        path = _write_yaml(
            tmp_path,
            {
                "questions": [
                    {"id": "t-01", "question": "Q?", "category": "jargon"}
                ]
            },
        )
        qs = load_questions(path)
        filtered = filter_questions(qs, ["edge_cases"])
        assert filtered == []


# ---------------------------------------------------------------------------
# Runner tests (dry run / stub)
# ---------------------------------------------------------------------------


class TestRunBenchmark:
    def test_dry_run_skips_all(self, questions):
        run = run_benchmark(questions, dry_run=True)
        assert all(r["status"] == "skipped" for r in run.results)
        assert len(run.results) == len(questions)

    def test_dry_run_no_answers(self, questions):
        run = run_benchmark(questions, dry_run=True)
        for r in run.results:
            assert r["answer"] is None
            assert r["sql_generated"] is None

    def test_stub_run_returns_answers(self, questions):
        run = run_benchmark(questions[:2], dry_run=False)
        for r in run.results:
            assert r["status"] == "success"
            assert r["answer"] is not None
            assert r["sql_generated"] is not None
            assert r["response_time_seconds"] is not None

    def test_summary_structure(self, questions):
        run = run_benchmark(questions, dry_run=True)
        s = run.summary
        assert "total_questions" in s
        assert s["total_questions"] == len(questions)
        assert "by_status" in s
        assert "by_category" in s

    def test_run_metadata(self, questions):
        run = run_benchmark(
            questions,
            dry_run=True,
            questions_file="test.yaml",
            filters=["jargon"],
        )
        assert run.questions_file == "test.yaml"
        assert run.filters == ["jargon"]
        assert run.dry_run is True
        assert run.run_id  # non-empty
        assert run.timestamp  # non-empty


# ---------------------------------------------------------------------------
# Output tests
# ---------------------------------------------------------------------------


class TestWriteResults:
    def test_writes_valid_json(self, questions, tmp_path):
        run = run_benchmark(questions[:3], dry_run=True)
        path = write_results(run, tmp_path)
        assert path.exists()
        assert path.suffix == ".json"

        with open(path) as f:
            data = json.load(f)

        assert data["run_id"] == run.run_id
        assert len(data["results"]) == 3

    def test_creates_output_dir(self, questions, tmp_path):
        nested = tmp_path / "a" / "b" / "c"
        run = run_benchmark(questions[:1], dry_run=True)
        path = write_results(run, nested)
        assert path.exists()

    def test_filename_contains_run_id(self, questions, tmp_path):
        run = run_benchmark(questions[:1], dry_run=True)
        path = write_results(run, tmp_path)
        assert run.run_id in path.name
