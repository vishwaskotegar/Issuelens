import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status

from issuelens import __version__
from issuelens.api.schemas import (
    HealthResponse,
    InvestigationCreateRequest,
    InvestigationCreateResponse,
    InvestigationStatusResponse,
    IssueIngestRequest,
    IssueIngestResponse,
    ReadinessResponse,
)
from issuelens.config import Settings, get_settings
from issuelens.core.errors import ResourceNotFoundError
from issuelens.github.client import GitHubClient, GitHubClientProtocol
from issuelens.github.url_parser import parse_issue_target

router = APIRouter()

# In-memory investigation registry until Phase 6 PostgreSQL persistence
_INVESTIGATIONS: dict[str, dict[str, Any]] = {}


def get_github_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GitHubClientProtocol:
    """Dependency provider for GitHub client."""
    return GitHubClient(
        token=settings.github_token,
        timeout_seconds=float(settings.request_timeout_seconds),
    )


@router.get(
    "/healthz",
    response_model=HealthResponse,
    tags=["Operations"],
    summary="Liveness probe",
    description="Returns service liveness status without exposing internal credentials.",
)
def get_health(settings: Annotated[Settings, Depends(get_settings)]) -> HealthResponse:
    """Liveness probe: verifies process is responsive."""
    return HealthResponse(
        status="ok",
        app="issuelens",
        environment=settings.app_env,
        version=__version__,
    )


@router.get(
    "/readyz",
    response_model=ReadinessResponse,
    tags=["Operations"],
    summary="Readiness probe",
    description="Validates that system configuration and dependencies are ready.",
)
def get_readiness(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessResponse:
    """Readiness probe: checks required sub-systems without exposing credentials."""
    checks = {
        "config": "ok" if settings.request_timeout_seconds > 0 else "error",
    }
    all_ready = all(v == "ok" for v in checks.values())
    return ReadinessResponse(
        status="ready" if all_ready else "not_ready",
        checks=checks,
    )


@router.post(
    "/v1/issues/ingest",
    response_model=IssueIngestResponse,
    tags=["GitHub Ingestion"],
    summary="Ingest public GitHub issue",
    description="Validates an issue URL/coordinates, fetches issue metadata and pinned commit SHA.",
)
async def ingest_issue(
    payload: IssueIngestRequest,
    client: Annotated[GitHubClientProtocol, Depends(get_github_client)],
) -> IssueIngestResponse:
    """Validate issue URL and retrieve full context from GitHub."""
    target = parse_issue_target(payload.issue_url)
    context = await client.fetch_issue_context(target)

    return IssueIngestResponse(
        issue_url=context.issue_url,
        repository=context.repository,
        issue_number=context.issue_number,
        title=context.issue.title,
        body=context.issue.body,
        state=context.issue.state,
        author=context.issue.author,
        labels=context.issue.labels,
        comments_count=context.issue.comments_count,
        default_branch=context.default_branch,
        commit_sha=context.commit_sha,
        fetched_at=context.fetched_at.isoformat(),
    )


@router.post(
    "/v1/investigations",
    response_model=InvestigationCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Investigations"],
    summary="Start an investigation",
    description="Accepts issue URL, ingests context, pins commit SHA, and initializes run.",
)
async def create_investigation(
    payload: InvestigationCreateRequest,
    client: Annotated[GitHubClientProtocol, Depends(get_github_client)],
) -> InvestigationCreateResponse:
    """Create a new investigation run from a public issue."""
    target = parse_issue_target(payload.issue_url)
    context = await client.fetch_issue_context(target)

    run_id = f"inv_{uuid.uuid4().hex[:12]}"
    now = datetime.now(tz=UTC).isoformat()

    investigation_record = {
        "run_id": run_id,
        "status": "ingested",
        "issue_url": context.issue_url,
        "repository": context.repository,
        "issue_number": context.issue_number,
        "commit_sha": context.commit_sha,
        "issue_title": context.issue.title,
        "created_at": now,
        "context": context.model_dump(),
    }
    _INVESTIGATIONS[run_id] = investigation_record

    return InvestigationCreateResponse(
        run_id=run_id,
        status="ingested",
        issue_url=context.issue_url,
        repository=context.repository,
        issue_number=context.issue_number,
        commit_sha=context.commit_sha,
        created_at=now,
    )


@router.get(
    "/v1/investigations/{run_id}",
    response_model=InvestigationStatusResponse,
    tags=["Investigations"],
    summary="Fetch investigation run status",
    description="Retrieves the current status, coordinates, and metadata of an investigation run.",
)
def get_investigation_status(run_id: str) -> InvestigationStatusResponse:
    """Retrieve investigation run by run_id."""
    record = _INVESTIGATIONS.get(run_id)
    if not record:
        raise ResourceNotFoundError(f"Investigation run '{run_id}' not found.")

    return InvestigationStatusResponse(
        run_id=record["run_id"],
        status=record["status"],
        issue_url=record["issue_url"],
        repository=record["repository"],
        issue_number=record["issue_number"],
        commit_sha=record["commit_sha"],
        created_at=record["created_at"],
        issue_title=record.get("issue_title"),
    )
