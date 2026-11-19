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


def test_cors_preflight_for_allowed_origin() -> None:
    """Verify CORS preflight returns correct access control headers for allowed origin."""
    custom_settings = Settings(
        cors_allowed_origins=["http://localhost:3000"],
    )
    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    headers = {
        "Origin": "http://localhost:3000",
        "Access-Control-Request-Method": "POST",
        "Access-Control-Request-Headers": "Content-Type,X-API-Key",
    }
    response = client.options("/v1/issues/ingest", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("access-control-allow-origin") == "http://localhost:3000"
