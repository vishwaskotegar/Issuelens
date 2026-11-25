"""Unit tests for trusted proxy extraction and anti-spoofing rate limit defense."""

from unittest.mock import MagicMock

from fastapi import Request
from fastapi.testclient import TestClient

from issuelens.api.security import get_client_identifier, get_rate_limiter, is_trusted_proxy
from issuelens.config import Settings, get_settings
from issuelens.main import create_app


def test_is_trusted_proxy_direct_matches() -> None:
    """Verify trusted proxy matching for exact IP, localhost, IPv6, and CIDRs."""
    trusted = ["127.0.0.1", "::1", "10.0.0.0/8", "proxy.internal"]

    assert is_trusted_proxy("127.0.0.1", trusted) is True
    assert is_trusted_proxy("::1", trusted) is True
    assert is_trusted_proxy("proxy.internal", trusted) is True
    assert is_trusted_proxy("10.1.2.3", trusted) is True
    assert is_trusted_proxy("10.254.254.254", trusted) is True

    # Untrusted IPs
    assert is_trusted_proxy("192.168.1.1", trusted) is False
    assert is_trusted_proxy("203.0.113.195", trusted) is False
    assert is_trusted_proxy("invalid-host", trusted) is False


def test_get_client_identifier_ignores_spoofed_header_from_untrusted_peer() -> None:
    """Verify X-Forwarded-For is ignored when the direct client socket is untrusted."""
    settings = Settings(
        trusted_proxies=["127.0.0.1", "10.0.0.1"],
    )

    # Simulated untrusted remote attacker (203.0.113.50) spoofing internal IP
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "203.0.113.50"
    request.headers = {"X-Forwarded-For": "198.51.100.1, 10.0.0.1"}

    client_id = get_client_identifier(request, settings)
    assert client_id == "203.0.113.50"


def test_get_client_identifier_honors_forwarded_header_from_trusted_proxy() -> None:
    """Verify X-Forwarded-For is accepted when the direct socket is a trusted proxy."""
    settings = Settings(
        trusted_proxies=["127.0.0.1", "10.0.0.1"],
    )

    # Simulated trusted load balancer (10.0.0.1) forwarding real user IP
    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    request.headers = {"X-Forwarded-For": "198.51.100.77, 10.0.0.1"}

    client_id = get_client_identifier(request, settings)
    assert client_id == "198.51.100.77"


def test_rate_limit_cannot_be_bypassed_by_spoofing_x_forwarded_for() -> None:
    """Verify an attacker cannot bypass the rate limiter by varying X-Forwarded-For."""
    limiter = get_rate_limiter()
    limiter.reset()

    # Configure app where TestClient (host 'testclient') is untrusted
    custom_settings = Settings(
        rate_limit_per_minute=2,
        trusted_proxies=["10.0.0.1"],  # 'testclient' is NOT in trusted_proxies
    )

    app = create_app()
    app.dependency_overrides[get_settings] = lambda: custom_settings
    client = TestClient(app)

    # Request 1 with spoofed header
    r1 = client.get("/v1/investigations/inv_1", headers={"X-Forwarded-For": "1.1.1.1"})
    assert r1.status_code == 404

    # Request 2 with different spoofed header
    r2 = client.get("/v1/investigations/inv_2", headers={"X-Forwarded-For": "2.2.2.2"})
    assert r2.status_code == 404

    # Request 3 with yet another spoofed header: must be blocked because direct host is tracked!
    r3 = client.get("/v1/investigations/inv_3", headers={"X-Forwarded-For": "3.3.3.3"})
    assert r3.status_code == 429
    assert r3.json()["code"] == "RATE_LIMIT_EXCEEDED"


def test_get_client_identifier_ignores_client_prepended_entries_behind_trusted_proxy() -> None:
    """Regression: proxies append to X-Forwarded-For, so left-most entries are client-controlled.

    An attacker behind a trusted proxy sends ``X-Forwarded-For: 1.2.3.4``; the proxy appends
    the real peer, producing ``1.2.3.4, 198.51.100.9``. The right-most untrusted hop is the
    true client.
    """
    settings = Settings(trusted_proxies=["10.0.0.1"])

    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    request.headers = {"X-Forwarded-For": "1.2.3.4, 198.51.100.9"}

    assert get_client_identifier(request, settings) == "198.51.100.9"


def test_get_client_identifier_skips_chained_trusted_proxies() -> None:
    """Right-most-untrusted walk skips every trusted hop in a multi-proxy chain."""
    settings = Settings(trusted_proxies=["10.0.0.0/8"])

    request = MagicMock(spec=Request)
    request.client = MagicMock()
    request.client.host = "10.0.0.1"
    request.headers = {"X-Forwarded-For": "6.6.6.6, 198.51.100.9, 10.0.0.2"}

    assert get_client_identifier(request, settings) == "198.51.100.9"
