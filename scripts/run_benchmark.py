#!/usr/bin/env python3
"""
Datatalk Benchmark Runner

Runs a curated set of questions against the Datatalk agent and captures
structured results. Phase 0 infrastructure for cheap model benchmarking,
SUQL evaluation, and LangGraph necessity testing.

Usage:
    python scripts/run_benchmark.py                     # Run all questions
    python scripts/run_benchmark.py --dry-run            # Validate only, no agent calls
    python scripts/run_benchmark.py --filter jargon      # Run one category
    python scripts/run_benchmark.py --filter known_failures,edge_cases
    python scripts/run_benchmark.py --questions path/to/questions.yaml
    python scripts/run_benchmark.py --output-dir path/to/results/
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUESTIONS = REPO_ROOT / "tests" / "benchmarks" / "questions.yaml"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "tests" / "benchmarks" / "results"

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

REQUIRED_FIELDS = {"id", "question", "category"}
OPTIONAL_FIELDS = {"expected_answer", "notes"}
ALL_FIELDS = REQUIRED_FIELDS | OPTIONAL_FIELDS
VALID_CATEGORIES = {"known_failures", "basic_working", "edge_cases", "jargon"}


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class BenchmarkQuestion:
    """A single benchmark question loaded from the YAML file."""

    id: str
    question: str
    category: str
    expected_answer: str | None = None
    notes: str | None = None


@dataclass
class BenchmarkResult:
    """Result of running a single benchmark question against the agent."""

    question_id: str
    question: str
    category: str
    answer: str | None = None
    sql_generated: str | None = None
    response_time_seconds: float | None = None
    cost_usd: float | None = None
    error: str | None = None
    status: str = "pending"  # pending | success | error | skipped


@dataclass
class BenchmarkRun:
    """A complete benchmark run with metadata and results."""

    run_id: str
    timestamp: str
    questions_file: str
    filters: list[str] = field(default_factory=list)
    dry_run: bool = False
    results: list[dict[str, Any]] = field(default_factory=list)
    summary: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Question loading and validation
# ---------------------------------------------------------------------------


def load_questions(path: Path) -> list[BenchmarkQuestion]:
    """Load and validate benchmark questions from a YAML file.

    Raises ValueError if the file is malformed or questions are invalid.
    """
    if not path.exists():
        raise FileNotFoundError(f"Questions file not found: {path}")

    with open(path) as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict) or "questions" not in data:
        raise ValueError("YAML file must contain a top-level 'questions' key")

    raw_questions = data["questions"]
    if not isinstance(raw_questions, list) or len(raw_questions) == 0:
        raise ValueError("'questions' must be a non-empty list")

    questions = []
    seen_ids: set[str] = set()

    for i, q in enumerate(raw_questions):
        if not isinstance(q, dict):
            raise ValueError(f"Question at index {i} is not a dict")

        # Check required fields
        missing = REQUIRED_FIELDS - set(q.keys())
        if missing:
            raise ValueError(
                f"Question at index {i} missing required fields: {missing}"
            )

        # Check for unknown fields
        unknown = set(q.keys()) - ALL_FIELDS
        if unknown:
            raise ValueError(
                f"Question '{q.get('id', f'index {i}')}' has unknown fields: {unknown}"
            )

        # Validate category
        if q["category"] not in VALID_CATEGORIES:
            raise ValueError(
                f"Question '{q['id']}' has invalid category '{q['category']}'. "
                f"Must be one of: {VALID_CATEGORIES}"
            )

        # Check for duplicate IDs
        if q["id"] in seen_ids:
            raise ValueError(f"Duplicate question ID: '{q['id']}'")
        seen_ids.add(q["id"])

        questions.append(
            BenchmarkQuestion(
                id=q["id"],
                question=q["question"],
                category=q["category"],
                expected_answer=q.get("expected_answer"),
                notes=q.get("notes"),
            )
        )

    return questions


def filter_questions(
    questions: list[BenchmarkQuestion], categories: list[str]
) -> list[BenchmarkQuestion]:
    """Filter questions by category."""
    invalid = set(categories) - VALID_CATEGORIES
    if invalid:
        raise ValueError(f"Invalid filter categories: {invalid}")
    return [q for q in questions if q.category in categories]


# ---------------------------------------------------------------------------
# Agent interface (stub)
# ---------------------------------------------------------------------------


def call_agent_stub(question: str) -> dict[str, Any]:
    """Stub agent call. Returns a mock response.

    Replace this with a real agent call when the Datatalk agent is available.
    The real implementation should return a dict with keys:
        answer: str           - The natural language answer
        sql_generated: str    - The SQL query that was generated
        cost_usd: float       - LLM cost for this query
    """
    return {
        "answer": f"[STUB] Mock answer for: {question}",
        "sql_generated": "SELECT 'stub' AS result;",
        "cost_usd": 0.0,
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_question(question: BenchmarkQuestion, dry_run: bool) -> BenchmarkResult:
    """Run a single benchmark question and return the result."""
    result = BenchmarkResult(
        question_id=question.id,
        question=question.question,
        category=question.category,
    )

    if dry_run:
        result.status = "skipped"
        return result

    start = time.monotonic()
    try:
        response = call_agent_stub(question.question)
        elapsed = time.monotonic() - start

        result.answer = response.get("answer")
        result.sql_generated = response.get("sql_generated")
        result.cost_usd = response.get("cost_usd")
        result.response_time_seconds = round(elapsed, 3)
        result.status = "success"
    except Exception as e:
        elapsed = time.monotonic() - start
        result.response_time_seconds = round(elapsed, 3)
        result.error = str(e)
        result.status = "error"

    return result


def run_benchmark(
    questions: list[BenchmarkQuestion],
    dry_run: bool = False,
    questions_file: str = "",
    filters: list[str] | None = None,
) -> BenchmarkRun:
    """Run the full benchmark suite and return structured results."""
    now = datetime.now(timezone.utc)
    run_id = now.strftime("%Y%m%d_%H%M%S")

    run = BenchmarkRun(
        run_id=run_id,
        timestamp=now.isoformat(),
        questions_file=questions_file,
        filters=filters or [],
        dry_run=dry_run,
    )

    results: list[BenchmarkResult] = []
    for q in questions:
        result = run_question(q, dry_run)
        results.append(result)

    run.results = [asdict(r) for r in results]
    run.summary = _compute_summary(results)
    return run


def _compute_summary(results: list[BenchmarkResult]) -> dict[str, Any]:
    """Compute summary statistics from benchmark results."""
    total = len(results)
    by_status: dict[str, int] = {}
    by_category: dict[str, dict[str, int]] = {}
    total_cost = 0.0
    response_times: list[float] = []

    for r in results:
        by_status[r.status] = by_status.get(r.status, 0) + 1

        if r.category not in by_category:
            by_category[r.category] = {}
        cat = by_category[r.category]
        cat[r.status] = cat.get(r.status, 0) + 1

        if r.cost_usd is not None:
            total_cost += r.cost_usd
        if r.response_time_seconds is not None:
            response_times.append(r.response_time_seconds)

    summary: dict[str, Any] = {
        "total_questions": total,
        "by_status": by_status,
        "by_category": by_category,
        "total_cost_usd": round(total_cost, 4),
    }

    if response_times:
        summary["avg_response_time_seconds"] = round(
            sum(response_times) / len(response_times), 3
        )
        summary["max_response_time_seconds"] = round(max(response_times), 3)

    return summary


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def write_results(run: BenchmarkRun, output_dir: Path) -> Path:
    """Write benchmark results to a timestamped JSON file."""
    output_dir.mkdir(parents=True, exist_ok=True)
    filename = f"benchmark_{run.run_id}.json"
    path = output_dir / filename

    with open(path, "w") as f:
        json.dump(asdict(run), f, indent=2)

    return path


def print_summary_table(run: BenchmarkRun) -> None:
    """Print a summary table of results to stdout.

    Uses rich if available, falls back to tabulate, then plain text.
    """
    rows = []
    for r in run.results:
        status_str = r["status"]
        time_str = (
            f"{r['response_time_seconds']:.3f}s"
            if r.get("response_time_seconds") is not None
            else "-"
        )
        cost_str = (
            f"${r['cost_usd']:.4f}"
            if r.get("cost_usd") is not None
            else "-"
        )
        answer_preview = ""
        if r.get("answer"):
            answer_preview = (
                r["answer"][:60] + "..." if len(r["answer"]) > 60 else r["answer"]
            )
        elif r.get("error"):
            answer_preview = f"ERROR: {r['error'][:50]}"

        rows.append(
            [r["question_id"], r["category"], status_str, time_str, cost_str, answer_preview]
        )

    headers = ["ID", "Category", "Status", "Time", "Cost", "Answer/Error"]

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title=f"Benchmark Run: {run.run_id}")
        for h in headers:
            table.add_column(h)
        for row in rows:
            table.add_row(*[str(c) for c in row])

        console.print()
        console.print(table)

        # Summary line
        s = run.summary
        console.print(
            f"\n[bold]Total:[/bold] {s['total_questions']} questions | "
            f"Cost: ${s['total_cost_usd']:.4f}"
        )
        if "avg_response_time_seconds" in s:
            console.print(
                f"[bold]Timing:[/bold] avg {s['avg_response_time_seconds']:.3f}s, "
                f"max {s['max_response_time_seconds']:.3f}s"
            )
        console.print()
        return
    except ImportError:
        pass

    try:
        from tabulate import tabulate

        print()
        print(f"Benchmark Run: {run.run_id}")
        print(tabulate(rows, headers=headers, tablefmt="grid"))
        s = run.summary
        print(f"\nTotal: {s['total_questions']} questions | Cost: ${s['total_cost_usd']:.4f}")
        if "avg_response_time_seconds" in s:
            print(
                f"Timing: avg {s['avg_response_time_seconds']:.3f}s, "
                f"max {s['max_response_time_seconds']:.3f}s"
            )
        print()
        return
    except ImportError:
        pass

    # Plain text fallback
    print(f"\nBenchmark Run: {run.run_id}")
    print("-" * 80)
    for row in rows:
        print("  ".join(str(c).ljust(15) for c in row))
    print("-" * 80)
    s = run.summary
    print(f"Total: {s['total_questions']} questions | Cost: ${s['total_cost_usd']:.4f}")
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run the Datatalk benchmark suite",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--questions",
        type=Path,
        default=DEFAULT_QUESTIONS,
        help=f"Path to questions YAML file (default: {DEFAULT_QUESTIONS})",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for result JSON files (default: {DEFAULT_OUTPUT_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate questions without calling the agent",
    )
    parser.add_argument(
        "--filter",
        type=str,
        default=None,
        help="Comma-separated list of categories to run (e.g., 'jargon,edge_cases')",
    )

    args = parser.parse_args()

    # Load questions
    try:
        questions = load_questions(args.questions)
    except (FileNotFoundError, ValueError) as e:
        print(f"Error loading questions: {e}", file=sys.stderr)
        return 1

    print(f"Loaded {len(questions)} questions from {args.questions}")

    # Filter
    filter_cats: list[str] | None = None
    if args.filter:
        filter_cats = [c.strip() for c in args.filter.split(",")]
        try:
            questions = filter_questions(questions, filter_cats)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 1
        print(f"Filtered to {len(questions)} questions in categories: {filter_cats}")

    if not questions:
        print("No questions to run after filtering.", file=sys.stderr)
        return 1

    if args.dry_run:
        print("Dry run: validating questions only, no agent calls")

    # Run benchmark
    run = run_benchmark(
        questions,
        dry_run=args.dry_run,
        questions_file=str(args.questions),
        filters=filter_cats,
    )

    # Output
    print_summary_table(run)

    result_path = write_results(run, args.output_dir)
    print(f"Results written to: {result_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
