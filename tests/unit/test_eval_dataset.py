"""Unit tests for evaluation dataset models and JSONL parser."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from issuelens.evaluation.dataset import (
    EvaluationCaseModel,
    EvaluationDataset,
    load_evaluation_cases,
    save_evaluation_cases,
)

SAMPLE_SHA = "0123456789abcdef0123456789abcdef01234567"


def test_evaluation_case_valid() -> None:
    """Test creating a valid EvaluationCaseModel."""
    case = EvaluationCaseModel(
        id="CASE-001",
        issue_url="https://github.com/pallets/flask/issues/4000",
        repo_full_name="pallets/flask",
        issue_number=4000,
        target_commit_sha=SAMPLE_SHA.upper(),  # Should be lowercased
        known_resolution="Fixed url prefix handling when blueprints are nested.",
        expected_files=["src\\flask\\blueprints.py", "src/flask/app.py"],
        notes="Blueprint routing bug",
    )
    assert case.id == "CASE-001"
    assert case.target_commit_sha == SAMPLE_SHA
    assert case.expected_files == ["src/flask/blueprints.py", "src/flask/app.py"]
    assert case.notes == "Blueprint routing bug"


def test_evaluation_case_invalid_sha() -> None:
    """Test validation failure on invalid commit SHA."""
    with pytest.raises(ValidationError) as exc:
        EvaluationCaseModel(
            id="CASE-001",
            issue_url="https://github.com/pallets/flask/issues/4000",
            repo_full_name="pallets/flask",
            issue_number=4000,
            target_commit_sha="not-a-sha",
            known_resolution="Fixed something.",
        )
    assert "target_commit_sha" in str(exc.value)


def test_evaluation_case_invalid_url() -> None:
    """Test validation failure on non-GitHub or malformed issue URL."""
    with pytest.raises(ValidationError) as exc:
        EvaluationCaseModel(
            id="CASE-001",
            issue_url="https://gitlab.com/pallets/flask/issues/4000",
            repo_full_name="pallets/flask",
            issue_number=4000,
            target_commit_sha=SAMPLE_SHA,
            known_resolution="Fixed something.",
        )
    assert "issue_url" in str(exc.value)


def test_evaluation_case_path_traversal() -> None:
    """Test validation rejection of path traversal in expected_files."""
    with pytest.raises(ValidationError) as exc:
        EvaluationCaseModel(
            id="CASE-001",
            issue_url="https://github.com/pallets/flask/issues/4000",
            repo_full_name="pallets/flask",
            issue_number=4000,
            target_commit_sha=SAMPLE_SHA,
            known_resolution="Fixed something.",
            expected_files=["../../etc/passwd"],
        )
    assert "expected_files" in str(exc.value)

    with pytest.raises(ValidationError) as exc:
        EvaluationCaseModel(
            id="CASE-001",
            issue_url="https://github.com/pallets/flask/issues/4000",
            repo_full_name="pallets/flask",
            issue_number=4000,
            target_commit_sha=SAMPLE_SHA,
            known_resolution="Fixed something.",
            expected_files=["/absolute/path.py"],
        )
    assert "expected_files" in str(exc.value)


def test_load_and_save_jsonl(tmp_path: Path) -> None:
    """Test serializing and deserializing cases using JSONL."""
    file_path = tmp_path / "cases.jsonl"
    case1 = EvaluationCaseModel(
        id="CASE-001",
        issue_url="https://github.com/pallets/flask/issues/4000",
        repo_full_name="pallets/flask",
        issue_number=4000,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="Fixed blueprint prefix parsing.",
        expected_files=["src/flask/blueprints.py"],
    )
    case2 = EvaluationCaseModel(
        id="CASE-002",
        issue_url="https://github.com/psf/requests/issues/5000",
        repo_full_name="psf/requests",
        issue_number=5000,
        target_commit_sha="a" * 40,
        known_resolution="Fixed header encoding in session.",
        expected_files=["requests/sessions.py"],
    )

    save_evaluation_cases([case1, case2], file_path)
    assert file_path.is_file()

    loaded = load_evaluation_cases(file_path)
    assert len(loaded) == 2
    assert loaded[0].id == "CASE-001"
    assert loaded[1].id == "CASE-002"


def test_load_jsonl_comments_and_whitespace(tmp_path: Path) -> None:
    """Test that comments and blank lines are safely skipped."""
    file_path = tmp_path / "cases_with_comments.jsonl"
    line_json = (
        '{"id": "CASE-001", "issue_url": "https://github.com/a/b/issues/1", '
        f'"repo_full_name": "a/b", "issue_number": 1, "target_commit_sha": "{SAMPLE_SHA}", '
        '"known_resolution": "Resolution 1"}\n'
    )
    content = f"# Benchmark Cases for IssueLens\n\n{line_json}\n# Another comment\n"
    file_path.write_text(content, encoding="utf-8")

    cases = load_evaluation_cases(file_path)
    assert len(cases) == 1
    assert cases[0].id == "CASE-001"


def test_load_jsonl_errors(tmp_path: Path) -> None:
    """Test error handling on invalid or duplicate lines in JSONL."""
    non_existent = tmp_path / "missing.jsonl"
    with pytest.raises(FileNotFoundError):
        load_evaluation_cases(non_existent)

    # Malformed JSON
    corrupt_json = tmp_path / "corrupt.jsonl"
    corrupt_json.write_text('{"id": "CASE-001"\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid JSON on line 1"):
        load_evaluation_cases(corrupt_json)

    # Schema validation failure
    bad_schema = tmp_path / "bad_schema.jsonl"
    bad_schema.write_text('{"id": "CASE-001"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="Schema validation error on line 1"):
        load_evaluation_cases(bad_schema)

    # Duplicate case IDs
    duplicate_ids = tmp_path / "duplicate.jsonl"
    case_json = (
        '{"id": "CASE-001", "issue_url": "https://github.com/a/b/issues/1", '
        f'"repo_full_name": "a/b", "issue_number": 1, "target_commit_sha": "{SAMPLE_SHA}", '
        '"known_resolution": "Res 1", "expected_files": []}\n'
    )
    duplicate_ids.write_text(case_json + case_json, encoding="utf-8")
    with pytest.raises(ValueError, match="Duplicate case id 'CASE-001' on line 2"):
        load_evaluation_cases(duplicate_ids)


def test_evaluation_dataset_operations(tmp_path: Path) -> None:
    """Test EvaluationDataset collection helper methods."""
    case1 = EvaluationCaseModel(
        id="CASE-001",
        issue_url="https://github.com/pallets/flask/issues/4000",
        repo_full_name="pallets/flask",
        issue_number=4000,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="Fixed blueprint prefix parsing.",
        expected_files=["src/flask/blueprints.py"],
    )
    case2 = EvaluationCaseModel(
        id="CASE-002",
        issue_url="https://github.com/psf/requests/issues/5000",
        repo_full_name="psf/requests",
        issue_number=5000,
        target_commit_sha="b" * 40,
        known_resolution="Fixed header encoding in session.",
        expected_files=["requests/sessions.py"],
    )

    dataset = EvaluationDataset([case1, case2])
    assert len(dataset) == 2
    assert dataset.get("CASE-001") == case1
    assert dataset.get("UNKNOWN") is None
    assert len(dataset.filter_by_repo("pallets/flask")) == 1
    assert len(dataset.filter_by_repo("unknown/repo")) == 0

    out_file = tmp_path / "dataset_out.jsonl"
    dataset.to_jsonl(out_file)
    reloaded = EvaluationDataset.from_jsonl(out_file)
    assert len(reloaded) == 2
