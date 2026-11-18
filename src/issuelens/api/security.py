"""API security, token authentication, and sliding-window rate limiting."""

from __future__ import annotations

import math
import secrets
import threading
import time
from typing import Annotated

from fastapi import Depends, Header, Request

from issuelens.config import Settings, get_settings
from issuelens.core.errors import AuthenticationError, RateLimitExceededError
from issuelens.core.logging import get_logger

logger = get_logger("issuelens.api.security")


class RateLimiter:
    """Thread-safe sliding-window in-memory rate limiter per client identifier."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: dict[str, list[float]] = {}

    def check_rate_limit(
        self,
        client_id: str,
        limit: int,
        window_seconds: float = 60.0,
    ) -> tuple[bool, int, float]:
        """Check whether client_id has exceeded rate limit within sliding window.

        Returns (is_allowed, current_count, retry_after_seconds).
        """
        now = time.monotonic()
        cutoff = now - window_seconds

        with self._lock:
            timestamps = self._requests.get(client_id, [])
            # Prune timestamps older than window cutoff
            pruned = [ts for ts in timestamps if ts > cutoff]

            if len(pruned) >= limit:
                earliest = pruned[0]
                retry_after = max(1.0, math.ceil(window_seconds - (now - earliest)))
                self._requests[client_id] = pruned
                return False, len(pruned), retry_after

            pruned.append(now)
            self._requests[client_id] = pruned
            return True, len(pruned), 0.0

    def reset(self) -> None:
        """Clear all rate limit tracking records."""
        with self._lock:
            self._requests.clear()


_global_rate_limiter = RateLimiter()


def get_rate_limiter() -> RateLimiter:
    """Dependency provider returning singleton RateLimiter instance."""
    return _global_rate_limiter


def get_client_identifier(request: Request) -> str:
    """Extract client IP address from X-Forwarded-For header or direct socket."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
        if client_ip:
            return client_ip

    if request.client and request.client.host:
        return request.client.host

    return "127.0.0.1"


def verify_api_key(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> str | None:
    """Verify API token from X-API-Key or Authorization Bearer header.

    If auth_required is False, requests proceed unauthenticated.
    """
    if not settings.auth_required:
        return None

    if settings.api_key is None or not settings.api_key.get_secret_value().strip():
        logger.error("Authentication requested but server has no API_KEY configured in Settings")
        raise AuthenticationError("API authentication is enabled on server but no API_KEY is set.")

    candidate_key = x_api_key

    # Check Authorization header if X-API-Key is missing
    if not candidate_key:
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            candidate_key = auth_header[7:].strip()

    if not candidate_key:
        raise AuthenticationError(
            "API key required. Provide via 'X-API-Key' header or 'Authorization: Bearer <token>'."
        )

    expected_key = settings.api_key.get_secret_value()
    if not secrets.compare_digest(candidate_key, expected_key):
        logger.warning(
            "Rejected unauthorized request with invalid API key from %s",
            get_client_identifier(request),
        )
        raise AuthenticationError("Invalid API key.")

    return candidate_key


def enforce_rate_limit(
    request: Request,
    settings: Annotated[Settings, Depends(get_settings)],
    limiter: Annotated[RateLimiter, Depends(get_rate_limiter)],
) -> None:
    """Enforce request rate limit per client IP or authenticated caller."""
    client_id = get_client_identifier(request)
    allowed, current_count, retry_after = limiter.check_rate_limit(
        client_id=client_id,
        limit=settings.rate_limit_per_minute,
        window_seconds=60.0,
    )

    if not allowed:
        logger.warning(
            "Rate limit exceeded for client %s (%d requests): retry after %ds",
            client_id,
            current_count,
            int(retry_after),
        )
        raise RateLimitExceededError(
            f"Rate limit exceeded: maximum {settings.rate_limit_per_minute} requests per minute. "
            f"Please retry after {int(retry_after)} seconds."
        )
