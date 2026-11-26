"""Unit tests for InvestigationService end-to-end report generation."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from issuelens.agent.llm import MockLLMClient
from issuelens.agent.service import (
    InvestigationService,
)
from issuelens.config import Settings
from issuelens.core.errors import (
    LLMOutputValidationError,
    ResourceNotFoundError,
)
from issuelens.github.client import GitHubClientProtocol
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
    GitHubRepo,
)
from issuelens.github.url_parser import IssueTarget
from issuelens.repository.snapshot import RepoSnapshot

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sample_repo"


class FakeGitHubClient(GitHubClientProtocol):
    """Fake GitHub client for investigation service tests."""

    def __init__(self, mode: str = "success") -> None:
        self.mode = mode

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        if self.mode == "404":
            raise ResourceNotFoundError(f"Issue {owner}/{repo}#{issue_number} not found.")

        return GitHubIssue(
            number=issue_number,
            title="Database connection timeout in authenticate",
            body="Users report timeout when connecting to pool in sample_pkg/auth.py",
            state="open",
            author="dev",
            labels=["bug"],
            created_at=datetime.now(tz=UTC),
            comments_count=1,
            html_url=f"https://github.com/{owner}/{repo}/issues/{issue_number}",
        )

    async def get_comments(
        self, _owner: str, _repo: str, _issue_number: int, _max: int | None = None
    ) -> list[GitHubComment]:
        return [
            GitHubComment(
                id=1,
                author="maintainer",
                body="Check database.py for pool timeout parameters.",
                created_at=datetime.now(tz=UTC),
            )
        ]

    async def get_repository(self, owner: str, repo: str) -> GitHubRepo:
        return GitHubRepo(
            owner=owner,
            name=repo,
            full_name=f"{owner}/{repo}",
            default_branch="main",
            is_private=False,
            html_url=f"https://github.com/{owner}/{repo}",
        )

    async def get_pinned_commit_sha(self, _owner: str, _repo: str, _ref: str) -> str:
        return "1122334455667788990011223344556677889900"

    async def download_archive(
        self, _owner: str, _repo: str, _ref: str, _max: int = 50_000_000
    ) -> bytes:
        return b""

    async def fetch_issue_context(self, target: IssueTarget) -> GitHubIssueContext:
        issue = await self.get_issue(target.owner, target.repo, target.issue_number)
        comments = await self.get_comments(target.owner, target.repo, target.issue_number)
        repo = await self.get_repository(target.owner, target.repo)
        sha = await self.get_pinned_commit_sha(target.owner, target.repo, repo.default_branch)

        return GitHubIssueContext(
            issue_url=target.canonical_url,
            repository=target.repo_full_name,
            issue_number=target.issue_number,
            default_branch=repo.default_branch,
            commit_sha=sha,
            issue=issue,
            comments=comments,
            fetched_at=datetime.now(tz=UTC),
        )


@pytest.mark.asyncio
async def test_investigation_service_full_flow() -> None:
    """Verify end-to-end investigation with snapshot and mocked LLM."""
    snapshot = RepoSnapshot(
        root_path=FIXTURES_DIR,
        repo_full_name="sample/repo",
        commit_sha="1122334455667788990011223344556677889900",
    )
    gh_client = FakeGitHubClient()
    llm_client = MockLLMClient()
    settings = Settings(app_env="test")

    service = InvestigationService(
        github_client=gh_client,
        llm_client=llm_client,
        settings=settings,
    )

    report = await service.investigate(
        target_or_url="https://github.com/sample/repo/issues/10",
        snapshot=snapshot,
    )

    assert report.run_id.startswith("inv_")
    assert report.repository == "sample/repo"
    assert report.commit_sha == "1122334455667788990011223344556677889900"
    assert report.verification_status == "verified"
    assert "Mock investigation identified probable cause" in report.summary
    assert len(report.evidence) > 0
    assert report.evidence[0].evidence_id == "ev_1"
    assert len(report.hypotheses) == 1
    assert report.hypotheses[0].confidence == "high"
    assert report.hypotheses[0].supporting_evidence_ids == ["ev_1"]


@pytest.mark.asyncio
async def test_investigation_service_malformed_llm_output() -> None:
    """Verify LLM returning invalid schema raises LLMOutputValidationError."""
    snapshot = RepoSnapshot(
        root_path=FIXTURES_DIR,
        repo_full_name="sample/repo",
        commit_sha="1122334455667788990011223344556677889900",
    )
    gh_client = FakeGitHubClient()
    llm_client = MockLLMClient(
        fail_with=LLMOutputValidationError("LangChain structured output failed validation")
    )
    settings = Settings(app_env="test")

    service = InvestigationService(
        github_client=gh_client,
        llm_client=llm_client,
        settings=settings,
    )

    with pytest.raises(LLMOutputValidationError, match="failed validation"):
        await service.investigate(
            target_or_url="https://github.com/sample/repo/issues/10",
            snapshot=snapshot,
        )


@pytest.mark.asyncio
async def test_investigation_service_upstream_404() -> None:
    """Verify upstream GitHub 404 is propagated directly."""
    snapshot = RepoSnapshot(
        root_path=FIXTURES_DIR,
        repo_full_name="sample/repo",
        commit_sha="1122334455667788990011223344556677889900",
    )
    gh_client = FakeGitHubClient(mode="404")
    llm_client = MockLLMClient()
    settings = Settings(app_env="test")

    service = InvestigationService(
        github_client=gh_client,
        llm_client=llm_client,
        settings=settings,
    )

    with pytest.raises(ResourceNotFoundError):
        await service.investigate(
            target_or_url="https://github.com/sample/repo/issues/999",
            snapshot=snapshot,
        )

