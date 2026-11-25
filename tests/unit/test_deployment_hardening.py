"""Unit and integration tests for Phase 9 deployment hardening, API auth, and rate limiting."""

from collections.abc import Generator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr

from issuelens.api.security import RateLimiter, get_rate_limiter
from issuelens.config import Settings, get_settings
from issuelens.main import create_app


@pytest.fixture(autouse=True)
def reset_limiter() -> Generator[None, None, None]:
    """Reset rate limiter singleton before and after every test."""
    limiter = get_rate_limiter()
    limiter.reset()
    yield
    limiter.reset()


def test_rate_limiter_sliding_window_logic() -> None:
    """Verify rate limiter allows requests up to limit and blocks when exceeded."""
    limiter = RateLimiter()
    client_ip = "192.168.1.100"
    limit = 3

    # First 3 requests must be allowed
    for _ in range(limit):
        allowed, count, _ = limiter.check_rate_limit(
            client_id=client_ip,
            limit=limit,
            window_seconds=60.0,
        )
        assert allowed is True
        assert count <= limit

    # 4th request must be rejected
    blocked, count, retry_after = limiter.check_rate_limit(
        client_id=client_ip,
        limit=limit,
        window_seconds=60.0,
    )
    assert blocked is False
    assert count == limit
    assert retry_after > 0.0

    # Distinct client must not be affected
    other_ip = "192.168.1.101"
    allowed_other, count_other, _ = limiter.check_rate_limit(
        client_id=other_ip,
        limit=limit,
        window_seconds=60.0,
    )
    assert allowed_other is True
    assert count_other == 1


def test_api_auth_disabled_by_default() -> None:
    """Verify endpoints work unauthenticated when auth_required=False."""
    custom_settings = Settings(
        app_env="local",
        auth_required=False,
        api_key=SecretStr(""),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"

    # /v1/ endpoints should also be accessible without auth header when auth_required=False
    v1_resp = client.get("/v1/investigations/inv_missing123")
    assert v1_resp.status_code == 404
    assert v1_resp.json()["code"] == "NOT_FOUND"


def test_health_and_readiness_remain_public_when_auth_enabled() -> None:
    """Verify operational probes (/healthz, /readyz) remain public when API key is required."""
    custom_settings = Settings(
        app_env="production",
        auth_required=True,
        api_key=SecretStr("production_super_secret"),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    # Health and readiness probes should never be blocked by auth
    h_resp = client.get("/healthz")
    assert h_resp.status_code == 200

    r_resp = client.get("/readyz")
    assert r_resp.status_code == 200


def test_api_auth_enforcement_missing_key() -> None:
    """Verify /v1/ routes reject requests with 401 when auth is required and no key provided."""
    custom_settings = Settings(
        app_env="production",
        auth_required=True,
        api_key=SecretStr("production_super_secret"),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    response = client.get("/v1/investigations/inv_nonexistent")
    assert response.status_code == 401
    payload = response.json()
    assert payload["code"] == "UNAUTHORIZED"
    assert "API key required" in payload["error"]


def test_api_auth_enforcement_valid_x_api_key() -> None:
    """Verify /v1/ routes accept requests authenticated via X-API-Key header."""
    custom_settings = Settings(
        app_env="production",
        auth_required=True,
        api_key=SecretStr("production_super_secret"),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    response = client.get(
        "/v1/investigations/inv_missing123",
        headers={"X-API-Key": "production_super_secret"},
    )
    # Auth succeeded; passes through to repository lookup (which returns 404 for unknown ID)
    assert response.status_code == 404
    assert response.json()["code"] == "NOT_FOUND"


def test_api_auth_enforcement_valid_bearer_token() -> None:
    """Verify /v1/ routes accept requests authenticated via Authorization: Bearer header."""
    custom_settings = Settings(
        app_env="production",
        auth_required=True,
        api_key=SecretStr("production_super_secret"),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    response = client.get(
        "/v1/investigations/inv_missing123",
        headers={"Authorization": "Bearer production_super_secret"},
    )
    assert response.status_code == 404


def test_api_auth_enforcement_invalid_key() -> None:
    """Verify /v1/ routes reject invalid API keys with 401."""
    custom_settings = Settings(
        app_env="production",
        auth_required=True,
        api_key=SecretStr("production_super_secret"),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    response = client.get(
        "/v1/investigations/inv_missing123",
        headers={"X-API-Key": "wrong_token_guess"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_rate_limit_middleware_blocks_with_429() -> None:
    """Verify client exceeding configured rate limit receives HTTP 429 and Retry-After header."""
    custom_settings = Settings(
        rate_limit_per_minute=2,
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    # 1st request: allowed
    r1 = client.get("/v1/investigations/inv_1")
    assert r1.status_code == 404

    # 2nd request: allowed
    r2 = client.get("/v1/investigations/inv_2")
    assert r2.status_code == 404

    # 3rd request: rate limit exceeded -> 429
    r3 = client.get("/v1/investigations/inv_3")
    assert r3.status_code == 429
    assert r3.json()["code"] == "RATE_LIMIT_EXCEEDED"
    assert "Retry-After" in r3.headers


def test_dockerfile_and_compose_configuration() -> None:
    """Verify Dockerfile and docker-compose.yml exist and satisfy production safety rules."""
    root = Path(__file__).resolve().parent.parent.parent
    dockerfile_path = root / "Dockerfile"
    compose_path = root / "docker-compose.yml"
    dockerignore_path = root / ".dockerignore"

    assert dockerfile_path.exists()
    assert compose_path.exists()
    assert dockerignore_path.exists()

    df_content = dockerfile_path.read_text(encoding="utf-8")
    # Multi-stage check
    assert "AS builder" in df_content
    assert "AS runtime" in df_content
    # Non-root user check
    assert "USER issuelens:issuelens" in df_content
    # Health check check
    assert "HEALTHCHECK" in df_content
    assert "curl -f http://localhost:8000/healthz" in df_content
    # Ripgrep check
    assert "ripgrep" in df_content

    compose_content = compose_path.read_text(encoding="utf-8")
    assert "issuelens-app" in compose_content
    assert "issuelens-db" in compose_content
    assert "postgres:16-alpine" in compose_content
    assert "pg_isready" in compose_content
    assert "127.0.0.1:5432:5432" in compose_content


def test_execute_endpoint_requires_api_key() -> None:
    """Regression: POST /v1/investigations/{id}/execute must be behind API key auth.

    Previously registered on the unauthenticated operational router, allowing anyone
    to trigger archive downloads and paid LLM calls.
    """
    custom_settings = Settings(
        app_env="production",
        auth_required=True,
        api_key=SecretStr("production_super_secret"),
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    response = client.post("/v1/investigations/inv_nonexistent/execute")
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


def test_execute_endpoint_is_rate_limited() -> None:
    """Regression: /execute must count against the per-client rate limit."""
    custom_settings = Settings(rate_limit_per_minute=1)
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    assert client.post("/v1/investigations/inv_1/execute").status_code == 404
    assert client.post("/v1/investigations/inv_2/execute").status_code == 429


def test_invalid_api_key_attempts_are_rate_limited() -> None:
    """Regression: rate limiting must run before auth so key guessing is throttled."""
    custom_settings = Settings(
        app_env="production",
        auth_required=True,
        api_key=SecretStr("production_super_secret"),
        rate_limit_per_minute=2,
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    bad = {"X-API-Key": "guess"}
    assert client.get("/v1/investigations/a", headers=bad).status_code == 401
    assert client.get("/v1/investigations/b", headers=bad).status_code == 401
    assert client.get("/v1/investigations/c", headers=bad).status_code == 429
