"""Leak prevention test suite verifying retrospective benchmark isolation.

Ensures IssueLens never ingests pull requests directly, isolates ground-truth
resolutions and expected files from agent reasoning state, and pins repository
snapshots to pre-fix commit SHAs.
"""

from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from issuelens.agent.report import InvestigationReport
from issuelens.config import Settings
from issuelens.core.errors import InvalidInputError
from issuelens.evaluation.dataset import EvaluationCaseModel
from issuelens.evaluation.run_eval import EvaluationRunner
from issuelens.github.client import GitHubClient
from issuelens.github.models import GitHubIssue, GitHubIssueContext
from issuelens.repository.snapshot import RepoSnapshot


@pytest.mark.asyncio
async def test_github_client_rejects_pull_request_target() -> None:
    """Verify GitHubClient rejects PR URLs and objects with InvalidInputError."""
    mock_client = AsyncMock()
    mock_response = MagicMock()
    mock_response.is_success = True
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "id": 123456,
        "number": 52741,
        "title": "fix(sap): accept list-shaped reasoning_content from Gemini 3.x",
        "pull_request": {"url": "https://api.github.com/repos/BerriAI/litellm/pulls/52741"},
        "state": "closed",
        "body": "Fixes #50893",
    }
    mock_client.request.return_value = mock_response

    client = GitHubClient(token="fake_token", http_client=mock_client)

    with pytest.raises(InvalidInputError, match="is a pull request, not an issue"):
        await client.get_issue("BerriAI", "litellm", 52741)


@pytest.mark.asyncio
async def test_evaluation_runner_withholds_ground_truth_from_agent() -> None:
    """Verify EvaluationRunner strictly withholds known_resolution and expected_files."""
    target_sha = "4b6d1559b47ccff7df1d7fc490e8af22f656870b"
    case = EvaluationCaseModel(
        id="CASE-001",
        issue_url="https://github.com/BerriAI/litellm/issues/50893",
        repo_full_name="BerriAI/litellm",
        issue_number=50893,
        target_commit_sha=target_sha,
        known_resolution="fix(sap): accept list-shaped reasoning_content from Gemini 3.x",
        expected_files=[
            "litellm/llms/sap/chat/handler.py",
            "litellm/llms/sap/chat/reasoning.py",
            "litellm/llms/sap/chat/transformation.py",
        ],
        notes="Real BerriAI/litellm bug case",
    )

    valid_report = InvestigationReport(
        run_id="inv_test_leak",
        issue_url=case.issue_url,
        repository=case.repo_full_name,
        commit_sha=target_sha,
        summary="Diagnosis of list-shaped reasoning_content issue",
        observed_facts=["Observed TypeError when building chunks."],
        hypotheses=[],
        evidence=[],
        missing_information=[],
        suggested_tests=[],
        recommended_next_steps=[],
        verification_status="verified",
        limitations=[],
    )

    mock_service = AsyncMock()
    mock_service.investigate.return_value = valid_report

    mock_snapshot = MagicMock(spec=RepoSnapshot)
    mock_snapshot.commit_sha = target_sha

    runner = EvaluationRunner(
        service=mock_service,
        snapshot_resolver=lambda _repo, _sha: mock_snapshot,
    )

    await runner.evaluate_case(case)

    # Verify service.investigate was called with the issue URL and pre-fix snapshot
    mock_service.investigate.assert_awaited_once_with(
        target_or_url=case.issue_url,
        snapshot=mock_snapshot,
    )

    # Verify call kwargs did not receive ground-truth resolution or expected files
    call_args = mock_service.investigate.call_args
    passed_args = str(call_args)
    assert case.known_resolution not in passed_args
    for exp_file in case.expected_files:
        assert exp_file not in passed_args


@pytest.mark.asyncio
async def test_leak_prevention_on_real_litellm_case_context(
    tmp_path: pytest.TempPathFactory,
) -> None:
    """Verify investigating case BerriAI/litellm#50893 pins to pre-fix SHA and isolates PR."""
    pre_fix_sha = "4b6d1559b47ccff7df1d7fc490e8af22f656870b"
    resolving_pr_num = 52741
    resolving_pr_sha = "57fddaea78caeaee4f9a8b57ce57f712ae9a8de9"

    mock_gh = AsyncMock()
    Settings(
        app_env="local",
        snapshot_cache_dir=str(tmp_path),
    )

    issue_obj = GitHubIssue(
        number=50893,
        title="stream_chunk_builder raises TypeError with list reasoning_content",
        body="TypeError: sequence item 0: expected str instance, list found",
        state="closed",
        author="tester",
        labels=["bug"],
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        closed_at=datetime.now(UTC),
        comments_count=0,
        html_url="https://github.com/BerriAI/litellm/issues/50893",
    )

    mock_gh.fetch_issue_context.return_value = GitHubIssueContext(
        issue_url="https://github.com/BerriAI/litellm/issues/50893",
        repository="BerriAI/litellm",
        issue_number=50893,
        default_branch="main",
        commit_sha=pre_fix_sha,
        issue=issue_obj,
        comments=[],
        fetched_at=datetime.now(UTC),
    )

    async def mock_get_issue(owner: str, repo: str, num: int) -> GitHubIssue:
        if num == resolving_pr_num:
            raise InvalidInputError(
                f"Resource '{owner}/{repo}#{num}' is a pull request, not an issue."
            )
        return cast(GitHubIssue, mock_gh.fetch_issue_context.return_value.issue)

    mock_gh.get_issue.side_effect = mock_get_issue

    # Verify PR access is denied
    with pytest.raises(InvalidInputError, match="is a pull request, not an issue"):
        await mock_gh.get_issue("BerriAI", "litellm", resolving_pr_num)

    # Ensure pre-fix SHA differs from resolving PR merge commit
    assert pre_fix_sha != resolving_pr_sha
