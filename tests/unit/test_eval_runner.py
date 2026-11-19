"""Unit tests for evaluation harness runner and CLI orchestration."""

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from issuelens.agent.report import (
    EvidenceItem,
    Hypothesis,
    InvestigationReport,
)
from issuelens.evaluation.dataset import (
    EvaluationCaseModel,
    save_evaluation_cases,
)
from issuelens.evaluation.run_eval import (
    EvaluationRunner,
    format_evaluation_summary,
    run_evaluation,
)
from issuelens.persistence.db import get_session_factory
from issuelens.persistence.models import (
    EvaluationCase as EvaluationCaseRecord,
)
from issuelens.persistence.models import (
    EvaluationResult as EvaluationResultRecord,
)

SAMPLE_SHA = "0123456789abcdef0123456789abcdef01234567"


def _make_sample_report(run_id: str, issue_url: str) -> InvestigationReport:
    ev = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        commit_sha=SAMPLE_SHA,
        excerpt='"""Authentication utilities."""',
    )
    hyp = Hypothesis(
        title="Token authentication validation bug",
        explanation="Token validation in sample_pkg/auth.py accepts empty tokens.",
        confidence="high",
        supporting_evidence_ids=["ev_1"],
    )
    return InvestigationReport(
        run_id=run_id,
        issue_url=issue_url,
        repository="sample/repo",
        commit_sha=SAMPLE_SHA,
        summary="Test report summary.",
        observed_facts=["Observed behavior."],
        hypotheses=[hyp],
        evidence=[ev],
        missing_information=[],
        suggested_tests=["tests/test_auth.py"],
        recommended_next_steps=["Verify token logic"],
        verification_status="verified",
        limitations=[],
    )


@pytest.mark.asyncio
async def test_evaluation_runner_single_case_success() -> None:
    """Test successful evaluation of a case with mocked service."""
    case = EvaluationCaseModel(
        id="CASE-001",
        issue_url="https://github.com/sample/repo/issues/100",
        repo_full_name="sample/repo",
        issue_number=100,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="Fixed token validation in auth.py.",
        expected_files=["sample_pkg/auth.py"],
    )

    mock_service = AsyncMock()
    mock_service.investigate.return_value = _make_sample_report("run_1", case.issue_url)

    runner = EvaluationRunner(service=mock_service, k_values=[1, 5])
    metrics, report = await runner.evaluate_case(case)

    # Verify service was called with the pinned pre-fix commit SHA
    mock_service.investigate.assert_awaited_once_with(
        target_or_url=case.issue_url,
        snapshot=None,
    )
    assert report is not None
    assert metrics.case_id == "CASE-001"
    assert metrics.status == "completed"
    assert metrics.recall_at_k[1] == 1.0
    assert metrics.citation_validity_rate == 1.0
    assert metrics.unsupported_claims_count == 0


@pytest.mark.asyncio
async def test_evaluation_runner_case_failure() -> None:
    """Test handling when investigation service raises an unexpected error."""
    case = EvaluationCaseModel(
        id="CASE-002",
        issue_url="https://github.com/sample/repo/issues/102",
        repo_full_name="sample/repo",
        issue_number=102,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="Fixed something.",
        expected_files=["sample_pkg/auth.py"],
    )

    mock_service = AsyncMock()
    mock_service.investigate.side_effect = RuntimeError("Rate limit exceeded")

    runner = EvaluationRunner(service=mock_service)
    metrics, report = await runner.evaluate_case(case)

    assert report is None
    assert metrics.status == "failed"
    assert metrics.error_reason == "Rate limit exceeded"
    assert metrics.citation_validity_rate == 0.0


@pytest.mark.asyncio
async def test_evaluation_runner_with_persistence() -> None:
    """Test that EvaluationRunner persists EvaluationCase and EvaluationResult records."""
    session_factory = get_session_factory()
    case = EvaluationCaseModel(
        id="CASE-003",
        issue_url="https://github.com/sample/repo/issues/103",
        repo_full_name="sample/repo",
        issue_number=103,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="Fixed database retry loop.",
        expected_files=["sample_pkg/database.py"],
        notes="Database transient error",
    )

    mock_service = AsyncMock()
    mock_service.investigate.return_value = _make_sample_report("run_3", case.issue_url)

    runner = EvaluationRunner(
        service=mock_service,
        session_factory=session_factory,
        model_id="gpt-4o-mini",
        prompt_version="v2.0",
    )
    await runner.evaluate_case(case)

    # Verify database persistence
    async with session_factory() as session:
        case_stmt = select(EvaluationCaseRecord).where(EvaluationCaseRecord.id == "CASE-003")
        case_rec = (await session.execute(case_stmt)).scalar_one_or_none()
        assert case_rec is not None
        assert case_rec.repo_full_name == "sample/repo"
        assert case_rec.target_commit_sha == SAMPLE_SHA

        res_stmt = select(EvaluationResultRecord).where(
            EvaluationResultRecord.case_id == "CASE-003"
        )
        res_rec = (await session.execute(res_stmt)).scalar_one_or_none()
        assert res_rec is not None
        assert res_rec.model_id == "gpt-4o-mini"
        assert res_rec.prompt_version == "v2.0"
        assert res_rec.metrics_json["status"] == "completed"


@pytest.mark.asyncio
async def test_run_evaluation_e2e_and_summary(tmp_path: Path) -> None:
    """Test run_evaluation end-to-end with filtering, JSON output, and summary formatting."""
    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.json"

    case1 = EvaluationCaseModel(
        id="CASE-001",
        issue_url="https://github.com/sample/repo/issues/101",
        repo_full_name="sample/repo",
        issue_number=101,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="Fixed token validation.",
        expected_files=["sample_pkg/auth.py"],
    )
    case2 = EvaluationCaseModel(
        id="CASE-002",
        issue_url="https://github.com/sample/repo/issues/102",
        repo_full_name="sample/repo",
        issue_number=102,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="Fixed database retry.",
        expected_files=["sample_pkg/database.py"],
    )
    save_evaluation_cases([case1, case2], cases_path)

    mock_service = AsyncMock()
    mock_service.investigate.side_effect = [
        _make_sample_report("run_1", case1.issue_url),
        _make_sample_report("run_2", case2.issue_url),
    ]

    runner = EvaluationRunner(service=mock_service, k_values=[1, 3, 5])
    report = await run_evaluation(
        cases_path=cases_path,
        output_path=results_path,
        runner=runner,
    )

    assert report.total_cases == 2
    assert report.completed_cases == 2
    assert results_path.is_file()

    # Verify JSON content
    with results_path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["total_cases"] == 2
    assert "mean_citation_validity" in data

    # Verify formatted summary string
    summary = format_evaluation_summary(report)
    assert "ISSUELENS BENCHMARK EVALUATION REPORT" in summary
    assert "Total Cases:               2" in summary
    assert "Citation Validity Rate:" in summary

    # Test filtering by case_id
    report_single = await run_evaluation(
        cases_path=cases_path,
        output_path=tmp_path / "single_res.json",
        case_id="CASE-001",
        runner=runner,
    )
    assert report_single.total_cases == 1
    assert report_single.case_metrics[0].case_id == "CASE-001"

    # Test filtering by unknown case_id raises ValueError
    with pytest.raises(ValueError, match="not found"):
        await run_evaluation(
            cases_path=cases_path,
            output_path=tmp_path / "out.json",
            case_id="UNKNOWN-ID",
            runner=runner,
        )


@pytest.mark.asyncio
async def test_run_evaluation_mock_mode_default(tmp_path: Path) -> None:
    """Verify default mock mode executes without injected runner and passes gates."""
    from issuelens.evaluation.run_eval import create_fixture_snapshot_resolver

    cases_path = tmp_path / "cases.jsonl"
    results_path = tmp_path / "results.json"

    case1 = EvaluationCaseModel(
        id="CASE-020",
        issue_url="https://github.com/sample/repo/issues/101",
        repo_full_name="sample/repo",
        issue_number=101,
        target_commit_sha=SAMPLE_SHA,
        known_resolution=(
            "Add token validation error check in validate_token to reject empty tokens."
        ),
        expected_files=["sample_pkg/auth.py"],
    )
    save_evaluation_cases([case1], cases_path)

    # run_evaluation with runner=None and live=False triggers MockInvestigationService
    report = await run_evaluation(
        cases_path=cases_path,
        output_path=results_path,
        runner=None,
        live=False,
    )

    assert report.total_cases == 1
    assert report.completed_cases == 1
    assert report.failed_cases == 0
    assert report.mean_citation_validity >= 0.95
    assert report.mean_recall_at_k.get(5, 0.0) >= 0.80
    assert report.benchmark_targets_met.get("citation_validity_ge_95") is True

    # Test fixture snapshot resolver
    resolver = create_fixture_snapshot_resolver()
    snap = resolver("sample/repo", SAMPLE_SHA)
    assert snap is not None
    assert snap.repo_full_name == "sample/repo"
    assert (snap.root_path / "sample_pkg" / "auth.py").is_file()

    assert resolver("other/repo", SAMPLE_SHA) is None
