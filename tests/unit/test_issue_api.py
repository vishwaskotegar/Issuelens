"""Unit tests for issue ingestion and investigation REST endpoints."""

from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from issuelens.api.routes import get_github_client
from issuelens.core.errors import (
    RateLimitExceededError,
    ResourceNotFoundError,
    UpstreamTimeoutError,
)
from issuelens.github.client import GitHubClientProtocol
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
    GitHubRepo,
)
from issuelens.github.url_parser import IssueTarget
from issuelens.main import create_app


class FakeGitHubClient(GitHubClientProtocol):
    """Fake GitHub client for testing API routes without network calls."""

    def __init__(self, mode: str = "success") -> None:
        self.mode = mode

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        if self.mode == "404":
            raise ResourceNotFoundError(f"Issue {owner}/{repo}#{issue_number} not found.")
        if self.mode == "rate_limit":
            raise RateLimitExceededError("GitHub rate limit exceeded.")
        if self.mode == "timeout":
            raise UpstreamTimeoutError("GitHub API timed out.")

        return GitHubIssue(
            number=issue_number,
            title="Async deadlock in pool",
            body="Reproduction steps below.",
            state="open",
            author="tester",
            labels=["bug"],
            created_at=datetime.now(tz=UTC),
            comments_count=1,
            html_url=f"https://github.com/{owner}/{repo}/issues/{issue_number}",
        )

    async def get_comments(
        self,
        _owner: str,
        _repo: str,
        _issue_number: int,
        _max_comments: int | None = None,
    ) -> list[GitHubComment]:
        return [
            GitHubComment(
                id=101,
                author="reviewer",
                body="Confirmed on v1.2",
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
        return "9876543210abcdef9876543210abcdef98765432"

    async def download_archive(
        self,
        _owner: str,
        _repo: str,
        _ref: str,
        _max_bytes: int = 50_000_000,
    ) -> bytes:
        return b"PK\x05\x06\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00\x00"

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


@pytest.fixture
def test_app() -> FastAPI:
    """Create test application instance with fake GitHub client."""
    app = create_app()
    fake_client = FakeGitHubClient(mode="success")
    app.dependency_overrides[get_github_client] = lambda: fake_client
    return app


def test_ingest_issue_endpoint_success(test_app: FastAPI) -> None:
    """Verify POST /v1/issues/ingest succeeds for a valid issue URL."""
    client = TestClient(test_app)
    response = client.post(
        "/v1/issues/ingest",
        json={"issue_url": "https://github.com/psf/requests/issues/1234"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["repository"] == "psf/requests"
    assert data["issue_number"] == 1234
    assert data["title"] == "Async deadlock in pool"
    assert data["commit_sha"] == "9876543210abcdef9876543210abcdef98765432"
    assert data["default_branch"] == "main"


def test_ingest_issue_endpoint_malformed_url(test_app: FastAPI) -> None:
    """Verify 400 Bad Request returned for malformed issue URL."""
    client = TestClient(test_app)
    response = client.post(
        "/v1/issues/ingest",
        json={"issue_url": "not-a-valid-url"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_INPUT"


def test_ingest_issue_endpoint_unsupported_host(test_app: FastAPI) -> None:
    """Verify 400 Bad Request returned for non-github host."""
    client = TestClient(test_app)
    response = client.post(
        "/v1/issues/ingest",
        json={"issue_url": "https://gitlab.com/owner/repo/issues/123"},
    )
    assert response.status_code == 400
    data = response.json()
    assert data["code"] == "INVALID_INPUT"


def test_ingest_issue_endpoint_not_found() -> None:
    """Verify 404 Not Found returned when issue does not exist upstream."""
    app = create_app()
    app.dependency_overrides[get_github_client] = lambda: FakeGitHubClient(mode="404")
    client = TestClient(app)

    response = client.post(
        "/v1/issues/ingest",
        json={"issue_url": "https://github.com/org/missing/issues/999"},
    )
    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"


def test_ingest_issue_endpoint_rate_limited() -> None:
    """Verify 429 returned when GitHub rate limits are triggered."""
    app = create_app()
    app.dependency_overrides[get_github_client] = lambda: FakeGitHubClient(mode="rate_limit")
    client = TestClient(app)

    response = client.post(
        "/v1/issues/ingest",
        json={"issue_url": "https://github.com/org/repo/issues/100"},
    )
    assert response.status_code == 429
    data = response.json()
    assert data["code"] == "RATE_LIMIT_EXCEEDED"


def test_ingest_issue_endpoint_timeout() -> None:
    """Verify 504 returned when GitHub upstream times out."""
    app = create_app()
    app.dependency_overrides[get_github_client] = lambda: FakeGitHubClient(mode="timeout")
    client = TestClient(app)

    response = client.post(
        "/v1/issues/ingest",
        json={"issue_url": "https://github.com/org/repo/issues/100"},
    )
    assert response.status_code == 504
    data = response.json()
    assert data["code"] == "TIMEOUT"


def test_investigation_creation_and_status(test_app: FastAPI) -> None:
    """Verify POST /v1/investigations creates run and GET retrieves it."""
    client = TestClient(test_app)
    create_resp = client.post(
        "/v1/investigations",
        json={"issue_url": "https://github.com/encode/uvicorn/issues/888"},
    )
    assert create_resp.status_code == 201
    created_data = create_resp.json()
    assert "run_id" in created_data
    assert created_data["status"] == "ingested"
    assert created_data["repository"] == "encode/uvicorn"
    assert created_data["commit_sha"] == "9876543210abcdef9876543210abcdef98765432"

    run_id = created_data["run_id"]

    # Now fetch run status
    status_resp = client.get(f"/v1/investigations/{run_id}")
    assert status_resp.status_code == 200
    status_data = status_resp.json()
    assert status_data["run_id"] == run_id
    assert status_data["repository"] == "encode/uvicorn"
    assert status_data["status"] == "ingested"
    assert status_data["issue_title"] == "Async deadlock in pool"


def test_investigation_not_found(test_app: FastAPI) -> None:
    """Verify GET /v1/investigations/{run_id} returns 404 for unknown run_id."""
    client = TestClient(test_app)
    response = client.get("/v1/investigations/nonexistent_run_id")
    assert response.status_code == 404
    data = response.json()
    assert data["code"] == "NOT_FOUND"
