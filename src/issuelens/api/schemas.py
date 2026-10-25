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
