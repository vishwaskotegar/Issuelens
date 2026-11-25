import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, status
from sqlalchemy.ext.asyncio import AsyncSession

from issuelens import __version__
from issuelens.agent.llm import LLMClientProtocol, get_llm_client
from issuelens.agent.service import InvestigationService
from issuelens.api.schemas import (
    HealthResponse,
    InvestigationCreateRequest,
    InvestigationCreateResponse,
    InvestigationStatusResponse,
    IssueIngestRequest,
    IssueIngestResponse,
    ReadinessResponse,
)
from issuelens.api.security import enforce_rate_limit, verify_api_key
from issuelens.config import Settings, get_settings
from issuelens.core.errors import ResourceNotFoundError
from issuelens.github.client import GitHubClient, GitHubClientProtocol
from issuelens.github.url_parser import parse_issue_target
from issuelens.persistence.db import check_database_health, get_db_session
from issuelens.persistence.repository import (
    InvestigationRepositoryProtocol,
    SQLAlchemyInvestigationRepository,
)

router = APIRouter()
v1_router = APIRouter(
    prefix="/v1",
    # Rate limiting runs before authentication so invalid-key guessing is throttled too.
    dependencies=[Depends(enforce_rate_limit), Depends(verify_api_key)],
)


def get_github_client(
    settings: Annotated[Settings, Depends(get_settings)],
) -> GitHubClientProtocol:
    """Dependency provider for GitHub client."""
    return GitHubClient(
        token=settings.github_token,
        timeout_seconds=float(settings.request_timeout_seconds),
    )


def get_llm_client_dep(
    settings: Annotated[Settings, Depends(get_settings)],
) -> LLMClientProtocol:
    """Dependency provider for LLM client adapter."""
    return get_llm_client(settings)


def get_investigation_service(
    settings: Annotated[Settings, Depends(get_settings)],
    github_client: Annotated[GitHubClientProtocol, Depends(get_github_client)],
    llm_client: Annotated[LLMClientProtocol, Depends(get_llm_client_dep)],
) -> InvestigationService:
    """Dependency provider for InvestigationService."""
    return InvestigationService(
        github_client=github_client,
        llm_client=llm_client,
        settings=settings,
    )


def get_investigation_repository(
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> InvestigationRepositoryProtocol:
    """Dependency provider for InvestigationRepository."""
    return SQLAlchemyInvestigationRepository(session)


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
async def get_readiness(
    settings: Annotated[Settings, Depends(get_settings)],
) -> ReadinessResponse:
    """Readiness probe: checks required sub-systems without exposing credentials."""
    db_healthy, _ = await check_database_health()
    checks = {
        "config": "ok" if settings.request_timeout_seconds > 0 else "error",
        "database": "ok" if db_healthy else "error",
    }
    all_ready = all(v == "ok" for v in checks.values())
    return ReadinessResponse(
        status="ready" if all_ready else "not_ready",
        checks=checks,
    )


@v1_router.post(
    "/issues/ingest",
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


@v1_router.post(
    "/investigations",
    response_model=InvestigationCreateResponse,
    status_code=status.HTTP_201_CREATED,
    tags=["Investigations"],
    summary="Start an investigation",
    description="Accepts issue URL, ingests context, and optionally executes investigation.",
)
async def create_investigation(
    payload: InvestigationCreateRequest,
    client: Annotated[GitHubClientProtocol, Depends(get_github_client)],
    service: Annotated[InvestigationService, Depends(get_investigation_service)],
    repo: Annotated[InvestigationRepositoryProtocol, Depends(get_investigation_repository)],
) -> InvestigationCreateResponse:
    """Create a new investigation run from a public issue."""
    target = parse_issue_target(payload.issue_url)
    context = await client.fetch_issue_context(target)

    run_id = f"inv_{uuid.uuid4().hex[:12]}"
    now = datetime.now(tz=UTC)
    current_status = "ingested"

    await repo.create_run(
        run_id=run_id,
        issue_url=context.issue_url,
        repo_full_name=context.repository,
        issue_number=context.issue_number,
        commit_sha=context.commit_sha,
        status="ingested",
        issue_title=context.issue.title,
    )

    if payload.execute:
        try:
            await repo.update_run_status(run_id=run_id, status="running")
            report = await service.investigate(target_or_url=target, run_id=run_id)
            await repo.save_report(run_id=run_id, report=report)
            current_status = "completed"
        except Exception as exc:
            await repo.update_run_status(
                run_id=run_id,
                status="failed",
                error_message=str(exc),
            )
            await repo.commit()
            raise

    return InvestigationCreateResponse(
        run_id=run_id,
        status=current_status,
        issue_url=context.issue_url,
        repository=context.repository,
        issue_number=context.issue_number,
        commit_sha=context.commit_sha,
        created_at=now.isoformat(),
    )


@v1_router.post(
    "/investigations/{run_id}/execute",
    response_model=InvestigationStatusResponse,
    tags=["Investigations"],
    summary="Execute an investigation run",
    description="Executes code search and LLM analysis for an existing ingested run.",
)
async def execute_investigation(
    run_id: str,
    service: Annotated[InvestigationService, Depends(get_investigation_service)],
    repo: Annotated[InvestigationRepositoryProtocol, Depends(get_investigation_repository)],
) -> InvestigationStatusResponse:
    """Execute investigation for an already ingested run."""
    run = await repo.get_run(run_id)
    if not run:
        raise ResourceNotFoundError(f"Investigation run '{run_id}' not found.")

    target = parse_issue_target(run.issue_url)
    try:
        await repo.update_run_status(run_id=run_id, status="running")
        report = await service.investigate(target_or_url=target, run_id=run_id)
        await repo.save_report(run_id=run_id, report=report)
        status_str = "completed"
        error_msg = None
    except Exception as exc:
        await repo.update_run_status(
            run_id=run_id,
            status="failed",
            error_message=str(exc),
        )
        await repo.commit()
        raise

    return InvestigationStatusResponse(
        run_id=run.id,
        status=status_str,
        issue_url=run.issue_url,
        repository=run.repo_full_name,
        issue_number=run.issue_number,
        commit_sha=run.commit_sha,
        created_at=run.created_at.isoformat(),
        issue_title=run.issue_title,
        report=report,
        error=error_msg,
    )


@v1_router.get(
    "/investigations/{run_id}",
    response_model=InvestigationStatusResponse,
    tags=["Investigations"],
    summary="Fetch investigation run status",
    description="Retrieves the status, coordinates, report, and metadata of an investigation run.",
)
async def get_investigation_status(
    run_id: str,
    repo: Annotated[InvestigationRepositoryProtocol, Depends(get_investigation_repository)],
) -> InvestigationStatusResponse:
    """Retrieve investigation run by run_id."""
    run = await repo.get_run(run_id)
    if not run:
        raise ResourceNotFoundError(f"Investigation run '{run_id}' not found.")

    report = await repo.get_report(run_id)

    return InvestigationStatusResponse(
        run_id=run.id,
        status=run.status,
        issue_url=run.issue_url,
        repository=run.repo_full_name,
        issue_number=run.issue_number,
        commit_sha=run.commit_sha,
        created_at=run.created_at.isoformat(),
        issue_title=run.issue_title,
        report=report,
        error=run.error_message,
    )


# Mount versioned API routes under the operational router
router.include_router(v1_router)
