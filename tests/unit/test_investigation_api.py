"""Unit tests for investigation execution REST endpoints."""

from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from issuelens.agent.llm import MockLLMClient
from issuelens.agent.report import InvestigationReport
from issuelens.agent.service import InvestigationService
from issuelens.api.routes import get_github_client, get_investigation_service
from issuelens.config import Settings
from issuelens.github.client import GitHubClientProtocol
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
    GitHubRepo,
)
from issuelens.github.url_parser import IssueTarget
from issuelens.main import create_app
from issuelens.repository.snapshot import RepoSnapshot

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sample_repo"


class ApiTestGitHubClient(GitHubClientProtocol):
    """GitHub client for API testing with local mock fixtures."""

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        return GitHubIssue(
            number=issue_number,
            title="Database pool timeout",
            body="Timeout in sample_pkg/database.py",
            state="open",
            author="tester",
            labels=["bug"],
            created_at=datetime.now(tz=UTC),
            comments_count=1,
            html_url=f"https://github.com/{owner}/{repo}/issues/{issue_number}",
        )

    async def get_comments(
        self, _owner: str, _repo: str, _issue_number: int, _max: int | None = None
    ) -> list[GitHubComment]:
        return []

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
        return "aabbccddeeff00112233445566778899aabbccdd"

    async def download_archive(
        self, _owner: str, _repo: str, _ref: str, _max: int = 50_000_000
    ) -> bytes:
        return b""

    async def fetch_issue_context(self, target: IssueTarget) -> GitHubIssueContext:
        issue = await self.get_issue(target.owner, target.repo, target.issue_number)
        repo = await self.get_repository(target.owner, target.repo)
        sha = await self.get_pinned_commit_sha(target.owner, target.repo, repo.default_branch)

        return GitHubIssueContext(
            issue_url=target.canonical_url,
            repository=target.repo_full_name,
            issue_number=target.issue_number,
            default_branch=repo.default_branch,
            commit_sha=sha,
            issue=issue,
            comments=[],
            fetched_at=datetime.now(tz=UTC),
        )


class PreloadedInvestigationService(InvestigationService):
    """InvestigationService that injects our fixture snapshot to avoid network calls."""

    async def investigate(
        self,
        target_or_url: str | IssueTarget,
        snapshot: RepoSnapshot | None = None,
        run_id: str | None = None,
    ) -> InvestigationReport:
        active_snapshot = snapshot or RepoSnapshot(
            root_path=FIXTURES_DIR,
            repo_full_name="sample/repo",
            commit_sha="aabbccddeeff00112233445566778899aabbccdd",
        )
        return await super().investigate(
            target_or_url=target_or_url,
            snapshot=active_snapshot,
            run_id=run_id,
        )


@pytest.fixture
def api_test_app() -> FastAPI:
    """FastAPI test instance with injected fake clients and fixture service."""
    app = create_app()
    gh = ApiTestGitHubClient()
    llm = MockLLMClient()
    settings = Settings(app_env="test")
    service = PreloadedInvestigationService(
        github_client=gh,
        llm_client=llm,
        settings=settings,
    )

    app.dependency_overrides[get_github_client] = lambda: gh
    app.dependency_overrides[get_investigation_service] = lambda: service
    return app


def test_create_investigation_with_execute_true(api_test_app: FastAPI) -> None:
    """Verify POST /v1/investigations with execute=True returns completed run and report."""
    client = TestClient(api_test_app)
    response = client.post(
        "/v1/investigations",
        json={
            "issue_url": "https://github.com/sample/repo/issues/10",
            "execute": True,
        },
    )
    assert response.status_code == 201
    data = response.json()
    assert data["status"] == "completed"
    run_id = data["run_id"]

    # Verify status endpoint returns the generated report
    status_resp = client.get(f"/v1/investigations/{run_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["status"] == "completed"
    assert status_data["report"] is not None
    assert status_data["report"]["run_id"] == run_id
    assert status_data["report"]["verification_status"] == "verified"
    assert len(status_data["report"]["hypotheses"]) > 0


def test_execute_investigation_endpoint(api_test_app: FastAPI) -> None:
    """Verify POST /v1/investigations/{run_id}/execute executes an ingested run."""
    client = TestClient(api_test_app)
    # First ingest without execution
    create_resp = client.post(
        "/v1/investigations",
        json={"issue_url": "https://github.com/sample/repo/issues/10"},
    )
    assert create_resp.status_code == 201
    run_id = create_resp.json()["run_id"]
    assert create_resp.json()["status"] == "ingested"

    # Now execute
    exec_resp = client.post(f"/v1/investigations/{run_id}/execute")
    assert exec_resp.status_code == 200
    exec_data = exec_resp.json()
    assert exec_data["status"] == "completed"
    assert exec_data["report"] is not None
    assert exec_data["report"]["run_id"] == run_id


def test_execute_investigation_not_found(api_test_app: FastAPI) -> None:
    """Verify POST /v1/investigations/{run_id}/execute returns 404 for unknown run."""
    client = TestClient(api_test_app)
    response = client.post("/v1/investigations/nonexistent_123/execute")
    assert response.status_code == 404
