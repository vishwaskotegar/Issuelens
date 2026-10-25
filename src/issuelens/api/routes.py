"""FastAPI operational and health endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends

from issuelens import __version__
from issuelens.api.schemas import HealthResponse, ReadinessResponse
from issuelens.config import Settings, get_settings

router = APIRouter(tags=["Operations"])


@router.get(
    "/healthz",
    response_model=HealthResponse,
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
    # For Phase 0, configuration check is all that's required for local readiness
    all_ready = all(v == "ok" for v in checks.values())
    return ReadinessResponse(
        status="ready" if all_ready else "not_ready",
        checks=checks,
    )
