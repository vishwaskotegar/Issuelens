"""Unit tests for investigation execution REST endpoints."""

import asyncio
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated
from unittest.mock import patch

import pytest
from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from issuelens.agent.llm import MockLLMClient
from issuelens.agent.report import InvestigationReport
from issuelens.agent.service import InvestigationService
from issuelens.api.routes import (
    get_github_client,
    get_investigation_repository,
    get_investigation_service,
)
from issuelens.config import Settings
from issuelens.core.errors import LLMOutputValidationError
from issuelens.github.client import GitHubClientProtocol
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
    GitHubRepo,
)
from issuelens.github.url_parser import IssueTarget
from issuelens.main import create_app
from issuelens.persistence.db import create_all_tables, get_db_session
from issuelens.persistence.repository import (
    InvestigationRepositoryProtocol,
    SQLAlchemyInvestigationRepository,
)
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
        commit_sha: str | None = None,
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
            commit_sha=commit_sha,
        )


@pytest.fixture
def api_test_app(tmp_path: Path) -> FastAPI:
    """FastAPI test instance with injected fake clients and repository."""
    app = create_app()
    gh = ApiTestGitHubClient()
    llm = MockLLMClient()
    settings = Settings(app_env="test")
    service = PreloadedInvestigationService(
        github_client=gh,
        llm_client=llm,
        settings=settings,
    )

    db_file = tmp_path / "api_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"
    engine = create_async_engine(db_url)
    asyncio.run(create_all_tables(engine))
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    async def get_test_db_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    def get_test_repo(
        session: Annotated[AsyncSession, Depends(get_test_db_session)],
    ) -> InvestigationRepositoryProtocol:
        return SQLAlchemyInvestigationRepository(session)

    app.dependency_overrides[get_github_client] = lambda: gh
    app.dependency_overrides[get_investigation_service] = lambda: service
    app.dependency_overrides[get_db_session] = get_test_db_session
    app.dependency_overrides[get_investigation_repository] = get_test_repo
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


def test_create_investigation_sync_failure_records_failed_status(
    api_test_app: FastAPI,
) -> None:
    """Verify sync execution failure records failed status in database."""
    client = TestClient(api_test_app)
    with patch(
        "issuelens.agent.service.InvestigationService.investigate",
        side_effect=LLMOutputValidationError("Simulated LLM schema parsing failure"),
    ):
        response = client.post(
            "/v1/investigations",
            json={"issue_url": "https://github.com/sample/repo/issues/10", "execute": True},
        )
        assert response.status_code == 502

    # Query the status endpoint to verify the run exists and has status 'failed'
    # First ingest without execute to obtain a known run_id
    resp = client.post(
        "/v1/investigations",
        json={"issue_url": "https://github.com/sample/repo/issues/10", "execute": False},
    )
    assert resp.status_code == 201
    run_id = resp.json()["run_id"]

    # Now execute with failure
    with patch(
        "issuelens.agent.service.InvestigationService.investigate",
        side_effect=LLMOutputValidationError("Simulated execution failure"),
    ):
        exec_fail_resp = client.post(f"/v1/investigations/{run_id}/execute")
        assert exec_fail_resp.status_code == 502

    # Verify status is recorded as failed in the database
    status_resp = client.get(f"/v1/investigations/{run_id}")
    assert status_resp.status_code == 200
    assert status_resp.json()["status"] == "failed"
    assert "Simulated execution failure" in str(status_resp.json()["error"])


def test_failed_run_stores_generic_error_for_unexpected_exceptions(
    api_test_app: FastAPI,
) -> None:
    """Internal exception text (paths, SQL, keys) must not be stored or returned."""
    client = TestClient(api_test_app, raise_server_exceptions=False)
    resp = client.post(
        "/v1/investigations",
        json={"issue_url": "https://github.com/sample/repo/issues/10", "execute": False},
    )
    run_id = resp.json()["run_id"]

    leaky = RuntimeError("boom at C:/srv/app/db.py with sk-abcdef1234567890abcdef1234567890")
    with patch("issuelens.agent.service.InvestigationService.investigate", side_effect=leaky):
        exec_resp = client.post(f"/v1/investigations/{run_id}/execute")
    assert exec_resp.status_code == 500
    assert exec_resp.headers["X-Content-Type-Options"] == "nosniff"
    assert exec_resp.headers["X-Frame-Options"] == "DENY"

    error = client.get(f"/v1/investigations/{run_id}").json()["error"]
    assert error.startswith("INTERNAL_ERROR:")
    assert "sk-" not in error and "db.py" not in error


def test_failed_run_redacts_secrets_in_domain_errors(api_test_app: FastAPI) -> None:
    """Domain errors keep their message, minus secrets, in storage and in the response."""
    client = TestClient(api_test_app)
    resp = client.post(
        "/v1/investigations",
        json={"issue_url": "https://github.com/sample/repo/issues/10", "execute": False},
    )
    run_id = resp.json()["run_id"]
    key = "sk-abcdef1234567890abcdef1234567890"
    with patch(
        "issuelens.agent.service.InvestigationService.investigate",
        side_effect=LLMOutputValidationError(f"provider echoed {key}"),
    ):
        exec_resp = client.post(f"/v1/investigations/{run_id}/execute")
    assert key not in exec_resp.text

    error = client.get(f"/v1/investigations/{run_id}").json()["error"]
    assert error.startswith("LLM_OUTPUT_VALIDATION_ERROR: provider echoed")
    assert key not in error


@pytest.mark.asyncio
async def test_investigate_uses_recorded_commit_not_a_fresh_resolution() -> None:
    """Regression: execute re-resolved the commit, so run.commit_sha and the report could differ.

    For a closed issue the default branch may have moved to a commit containing the fix.
    """
    recorded_sha = "1111111111111111111111111111111111111111"
    captured: dict[str, str] = {}

    def fake_cached_snapshot(**kwargs: object) -> RepoSnapshot:
        captured["commit_sha"] = str(kwargs["commit_sha"])
        return RepoSnapshot(
            root_path=FIXTURES_DIR, repo_full_name="sample/repo", commit_sha=recorded_sha
        )

    service = InvestigationService(
        github_client=ApiTestGitHubClient(),  # resolves aabbcc... "now"
        llm_client=MockLLMClient(),
        settings=Settings(app_env="test"),
    )
    with patch("issuelens.agent.service.get_cached_snapshot", side_effect=fake_cached_snapshot):
        report = await service.investigate(
            target_or_url="https://github.com/sample/repo/issues/10",
            commit_sha=recorded_sha,
        )

    assert captured["commit_sha"] == recorded_sha
    assert report.commit_sha == recorded_sha
