"""Leak prevention test suite verifying retrospective benchmark isolation.

Ensures IssueLens never ingests pull requests directly, isolates ground-truth
resolutions and expected files from agent reasoning state, and pins repository
snapshots to pre-fix commit SHAs.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from issuelens.agent.report import InvestigationReport
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
        commit_sha=target_sha,
    )

    # Verify call kwargs did not receive ground-truth resolution or expected files
    call_args = mock_service.investigate.call_args
    passed_args = str(call_args)
    assert case.known_resolution not in passed_args
    for exp_file in case.expected_files:
        assert exp_file not in passed_args


@pytest.mark.asyncio
async def test_eval_client_withholds_comments_closed_state_and_live_issue_edits() -> None:
    """Regression: the agent received every issue comment and the 'closed' state.

    Docs claimed post-report discussion was withheld, but live mode passed up to 100
    comments into the prompt and document index. Comments often discuss or link the fix,
    and 'closed' tells the model a fix exists.
    """
    from issuelens.evaluation.run_eval import EvaluationGitHubClient
    from issuelens.github.models import GitHubComment
    from issuelens.github.url_parser import IssueTarget

    pre_fix_sha = "4b6d1559b47ccff7df1d7fc490e8af22f656870b"
    case = EvaluationCaseModel(
        id="CASE-L",
        issue_url="https://github.com/BerriAI/litellm/issues/50893",
        repo_full_name="BerriAI/litellm",
        issue_number=50893,
        target_commit_sha=pre_fix_sha,
        known_resolution="fix(sap): accept list-shaped reasoning_content",
        expected_files=["litellm/llms/sap/chat/handler.py"],
        issue_title="Original title as reported",
        issue_body="Original body as reported",
    )
    live_issue = GitHubIssue(
        number=50893,
        title="[FIXED in #52741] edited title",
        body="Edited after the fix: see litellm/llms/sap/chat/handler.py",
        state="closed",
        author="tester",
        labels=["bug"],
        created_at=datetime.now(UTC),
        closed_at=datetime.now(UTC),
        comments_count=1,
        html_url=case.issue_url,
    )
    fix_comment = GitHubComment(
        id=1,
        author="maintainer",
        body="Fixed by #52741 in litellm/llms/sap/chat/handler.py",
        created_at=datetime.now(UTC),
        html_url=f"{case.issue_url}#issuecomment-1",
    )
    real = AsyncMock()
    real.fetch_issue_context.return_value = GitHubIssueContext(
        issue_url=case.issue_url,
        repository="BerriAI/litellm",
        issue_number=50893,
        default_branch="main",
        commit_sha="ffffffffffffffffffffffffffffffffffffffff",  # default-branch HEAD
        issue=live_issue,
        comments=[fix_comment],
        fetched_at=datetime.now(UTC),
    )

    client = EvaluationGitHubClient(real, cases=[case])
    context = await client.fetch_issue_context(
        IssueTarget(owner="BerriAI", repo="litellm", issue_number=50893)
    )

    assert context.comments == []
    assert context.issue.state == "open"
    assert context.issue.closed_at is None
    assert context.issue.title == "Original title as reported"
    assert context.issue.body == "Original body as reported"
    assert context.commit_sha == pre_fix_sha
    assert "52741" not in context.model_dump_json()
