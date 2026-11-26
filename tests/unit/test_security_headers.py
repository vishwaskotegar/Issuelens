"""Unit tests for HTTP security headers and CORS protection middleware."""

from fastapi.testclient import TestClient
from pydantic import SecretStr

from issuelens.config import Settings, get_settings
from issuelens.main import create_app


def test_standard_security_headers_present_on_root_endpoint() -> None:
    """Verify standard defensive security headers are attached to responses."""
    app = create_app()
    client = TestClient(app)

    response = client.get("/")
    assert response.status_code == 200

    headers = response.headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "geolocation=()" in headers["Permissions-Policy"]


def test_hsts_header_present_in_production_environment() -> None:
    """Verify Strict-Transport-Security (HSTS) is set when app_env is production."""
    custom_settings = Settings(
        app_env="production",
        auth_required=True,
        api_key=SecretStr("prod-secret-token"),
    )
    app = create_app(custom_settings)
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200
    assert "Strict-Transport-Security" in response.headers
    assert "max-age=31536000" in response.headers["Strict-Transport-Security"]


def test_hsts_header_omitted_in_local_http() -> None:
    """Verify HSTS is omitted in local HTTP development to prevent forced SSL issues."""
    custom_settings = Settings(
        app_env="local",
    )
    app = create_app(custom_settings)
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    response = client.get("/healthz")
    assert response.status_code == 200
    assert "Strict-Transport-Security" not in response.headers


def test_security_headers_retained_on_error_responses() -> None:
    """Verify security headers are still emitted on 404 and exception responses."""
    app = create_app()
    client = TestClient(app)

    response = client.get("/nonexistent-route-for-testing")
    assert response.status_code == 404
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"


def _preflight(app_origins: list[str], origin: str) -> dict[str, str]:
    # Settings must go into create_app(): CORSMiddleware reads the origin list once at
    # construction, so a get_settings dependency override would not reach it.
    app = create_app(Settings(_env_file=None, cors_allowed_origins=app_origins))  # type: ignore[call-arg]
    client = TestClient(app)
    response = client.options(
        "/v1/issues/ingest",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type,X-API-Key",
        },
    )
    return dict(response.headers)


def test_cors_preflight_for_allowed_origin() -> None:
    """A configured origin (not one of the defaults) is allowed."""
    headers = _preflight(["https://ui.example.test"], "https://ui.example.test")
    assert headers.get("access-control-allow-origin") == "https://ui.example.test"


def test_cors_preflight_rejects_unlisted_origin() -> None:
    """An origin outside the allowlist, including a default one, gets no CORS grant."""
    for origin in ("https://evil.example.test", "http://localhost:3000"):
        headers = _preflight(["https://ui.example.test"], origin)
        assert "access-control-allow-origin" not in headers
