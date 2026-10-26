"""API request and response schemas."""

from typing import Any

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    """Response model for /healthz liveness probe."""

    status: str = Field(default="ok", description="Liveness status")
    app: str = Field(default="issuelens", description="Application identifier")
    environment: str = Field(..., description="Active runtime environment")
    version: str = Field(..., description="Application version")


class ReadinessResponse(BaseModel):
    """Response model for /readyz readiness probe."""

    status: str = Field(default="ready", description="Readiness status")
    checks: dict[str, str] = Field(
        default_factory=dict,
        description="Health status of external and internal dependencies",
    )


class ErrorDetail(BaseModel):
    """Structured error payload."""

    error: str = Field(..., description="Error message")
    code: str = Field(..., description="Machine-readable error code")
    details: list[dict[str, Any]] | None = Field(
        default=None,
        description="Optional detailed diagnostics",
    )


class IssueIngestRequest(BaseModel):
    """Payload for submitting a GitHub issue for ingestion."""

    issue_url: str = Field(
        ...,
        description="Public GitHub issue URL or coordinates (e.g., 'owner/repo#123')",
        examples=["https://github.com/psf/requests/issues/1234"],
    )


class IssueIngestResponse(BaseModel):
    """Structured response containing ingested issue metadata and context."""

    issue_url: str = Field(..., description="Canonical issue URL")
    repository: str = Field(..., description="Target repository: owner/repo")
    issue_number: int = Field(..., description="Issue number")
    title: str = Field(..., description="Issue title")
    body: str = Field(default="", description="Untrusted raw issue body")
    state: str = Field(default="open", description="Issue state (open/closed)")
    author: str | None = Field(default=None, description="Issue creator username")
    labels: list[str] = Field(default_factory=list, description="Labels on issue")
    comments_count: int = Field(default=0, description="Total number of comments")
    default_branch: str = Field(..., description="Default repository branch")
    commit_sha: str = Field(..., description="Pinned repository commit SHA")
    fetched_at: str = Field(..., description="ISO 8601 timestamp of fetch")


class InvestigationCreateRequest(BaseModel):
    """Payload for creating a new investigation run."""

    issue_url: str = Field(
        ...,
        description="Public GitHub issue URL or coordinates",
        examples=["https://github.com/psf/requests/issues/1234"],
    )


class InvestigationCreateResponse(BaseModel):
    """Response returned upon initiating an investigation."""

    run_id: str = Field(..., description="Unique investigation run identifier")
    status: str = Field(..., description="Current status of the investigation run")
    issue_url: str = Field(..., description="Canonical issue URL")
    repository: str = Field(..., description="Target repository: owner/repo")
    issue_number: int = Field(..., description="Target issue number")
    commit_sha: str = Field(..., description="Pinned commit SHA representing repository snapshot")
    created_at: str = Field(..., description="Timestamp of investigation creation")


class InvestigationStatusResponse(BaseModel):
    """Response for fetching run status and context."""

    run_id: str = Field(..., description="Unique investigation run identifier")
    status: str = Field(..., description="Current status of the investigation run")
    issue_url: str = Field(..., description="Canonical issue URL")
    repository: str = Field(..., description="Target repository: owner/repo")
    issue_number: int = Field(..., description="Target issue number")
    commit_sha: str = Field(..., description="Pinned commit SHA representing repository snapshot")
    created_at: str = Field(..., description="Timestamp of investigation creation")
    issue_title: str | None = Field(default=None, description="Title of the investigated issue")

