"""Unit tests for operational health and readiness endpoints."""

from fastapi.testclient import TestClient

from issuelens import __version__
from issuelens.main import create_app


def test_root_endpoint() -> None:
    """Verify root endpoint responds with basic service info."""
    client = TestClient(create_app())
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "IssueLens API"
    assert data["version"] == __version__
    assert data["status"] == "online"


def test_healthz_liveness() -> None:
    """Verify /healthz returns 200 with ok status and environment without leaking secrets."""
    client = TestClient(create_app())
    response = client.get("/healthz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == "issuelens"
    assert "environment" in data
    assert data["version"] == __version__
    # Ensure no credentials or internal limits are exposed
    assert "api_key" not in data
    assert "token" not in data
    assert "database_url" not in data


def test_readyz_readiness() -> None:
    """Verify /readyz returns 200 with readiness status."""
    client = TestClient(create_app())
    response = client.get("/readyz")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ready"
    assert "checks" in data
    assert data["checks"].get("config") == "ok"
