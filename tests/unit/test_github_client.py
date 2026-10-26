"""Unit tests for GitHub API client adapter using mocked network responses."""

import httpx
import pytest

from issuelens.core.errors import (
    InvalidInputError,
    RateLimitExceededError,
    ResourceNotFoundError,
    SafetyViolationError,
    UpstreamTimeoutError,
)
from issuelens.github.client import GitHubClient
from issuelens.github.url_parser import IssueTarget

SAMPLE_ISSUE_PAYLOAD = {
    "number": 1234,
    "title": "Bug in query parameter encoding",
    "body": "When passing special characters, query parameters are incorrectly escaped.",
    "state": "open",
    "user": {"login": "testuser"},
    "labels": [{"name": "bug"}, {"name": "needs-triage"}],
    "created_at": "2026-10-20T10:00:00Z",
    "updated_at": "2026-10-21T12:00:00Z",
    "closed_at": None,
    "comments": 2,
    "html_url": "https://github.com/psf/requests/issues/1234",
}

SAMPLE_COMMENTS_PAYLOAD = [
    {
        "id": 9001,
        "user": {"login": "maintainer"},
        "body": "Thanks for reporting. Can you provide a minimal reproduction script?",
        "created_at": "2026-10-20T11:00:00Z",
        "updated_at": None,
        "html_url": "https://github.com/psf/requests/issues/1234#issuecomment-9001",
    },
    {
        "id": 9002,
        "user": {"login": "testuser"},
        "body": "Here is the snippet: requests.get('https://example.com?q=%20')",
        "created_at": "2026-10-20T11:30:00Z",
        "updated_at": None,
        "html_url": "https://github.com/psf/requests/issues/1234#issuecomment-9002",
    },
]

SAMPLE_REPO_PAYLOAD = {
    "name": "requests",
    "full_name": "psf/requests",
    "owner": {"login": "psf"},
    "default_branch": "main",
    "private": False,
    "html_url": "https://github.com/psf/requests",
    "description": "A simple, yet elegant, HTTP library.",
}

SAMPLE_COMMIT_PAYLOAD = {
    "sha": "abcdef1234567890abcdef1234567890abcdef12",
}


@pytest.mark.asyncio
async def test_get_issue_success() -> None:
    """Verify issue metadata is accurately retrieved and parsed."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/psf/requests/issues/1234"
        return httpx.Response(200, json=SAMPLE_ISSUE_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        issue = await client.get_issue("psf", "requests", 1234)

        assert issue.number == 1234
        assert issue.title == "Bug in query parameter encoding"
        assert issue.author == "testuser"
        assert issue.labels == ["bug", "needs-triage"]
        assert issue.comments_count == 2
        assert not issue.is_pull_request


@pytest.mark.asyncio
async def test_get_comments_success() -> None:
    """Verify issue comments are fetched and parsed."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/psf/requests/issues/1234/comments"
        return httpx.Response(200, json=SAMPLE_COMMENTS_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        comments = await client.get_comments("psf", "requests", 1234)

        assert len(comments) == 2
        assert comments[0].id == 9001
        assert comments[0].author == "maintainer"
        assert "minimal reproduction" in comments[0].body
        assert comments[1].id == 9002


@pytest.mark.asyncio
async def test_get_repository_success() -> None:
    """Verify repository metadata and default branch extraction."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/psf/requests"
        return httpx.Response(200, json=SAMPLE_REPO_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        repo = await client.get_repository("psf", "requests")

        assert repo.name == "requests"
        assert repo.default_branch == "main"
        assert not repo.is_private


@pytest.mark.asyncio
async def test_get_pinned_commit_sha_success() -> None:
    """Verify commit SHA resolution."""

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/repos/psf/requests/commits/main"
        return httpx.Response(200, json=SAMPLE_COMMIT_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        sha = await client.get_pinned_commit_sha("psf", "requests", "main")
        assert sha == "abcdef1234567890abcdef1234567890abcdef12"


@pytest.mark.asyncio
async def test_fetch_issue_context_full_flow() -> None:
    """Verify full orchestration of issue context fetching."""

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/psf/requests/issues/1234":
            return httpx.Response(200, json=SAMPLE_ISSUE_PAYLOAD)
        if path == "/repos/psf/requests/issues/1234/comments":
            return httpx.Response(200, json=SAMPLE_COMMENTS_PAYLOAD)
        if path == "/repos/psf/requests":
            return httpx.Response(200, json=SAMPLE_REPO_PAYLOAD)
        if path == "/repos/psf/requests/commits/main":
            return httpx.Response(200, json=SAMPLE_COMMIT_PAYLOAD)
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        target = IssueTarget(owner="psf", repo="requests", issue_number=1234)
        context = await client.fetch_issue_context(target)

        assert context.issue_url == "https://github.com/psf/requests/issues/1234"
        assert context.repository == "psf/requests"
        assert context.issue_number == 1234
        assert context.default_branch == "main"
        assert context.commit_sha == "abcdef1234567890abcdef1234567890abcdef12"
        assert context.issue.title == "Bug in query parameter encoding"
        assert len(context.comments) == 2


@pytest.mark.asyncio
async def test_handle_404_not_found() -> None:
    """Verify 404 response raises ResourceNotFoundError."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        with pytest.raises(ResourceNotFoundError, match="not found"):
            await client.get_issue("nonexistent", "repo", 999)


@pytest.mark.asyncio
async def test_handle_rate_limit_exceeded_header() -> None:
    """Verify 403 with x-ratelimit-remaining=0 raises RateLimitExceededError."""

    def handler(_request: httpx.Request) -> httpx.Response:
        headers = {
            "x-ratelimit-remaining": "0",
            "x-ratelimit-reset": "1700000000",
        }
        return httpx.Response(403, headers=headers, json={"message": "API rate limit exceeded"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        with pytest.raises(RateLimitExceededError, match="rate limit exceeded"):
            await client.get_issue("psf", "requests", 1234)


@pytest.mark.asyncio
async def test_handle_http_429_rate_limit() -> None:
    """Verify HTTP 429 raises RateLimitExceededError."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"retry-after": "60"}, text="Too Many Requests")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        with pytest.raises(RateLimitExceededError, match="Retry after 60 seconds"):
            await client.get_issue("psf", "requests", 1234)


@pytest.mark.asyncio
async def test_handle_timeout() -> None:
    """Verify request timeout raises UpstreamTimeoutError."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("Connection timed out")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http, timeout_seconds=5.0)
        with pytest.raises(UpstreamTimeoutError, match="timed out"):
            await client.get_issue("psf", "requests", 1234)


@pytest.mark.asyncio
async def test_reject_pull_request() -> None:
    """Verify that issues that are actually pull requests are rejected."""
    pr_payload = dict(SAMPLE_ISSUE_PAYLOAD)
    pr_payload["pull_request"] = {"url": "https://api.github.com/repos/psf/requests/pulls/1234"}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=pr_payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        with pytest.raises(InvalidInputError, match="is a pull request, not an issue"):
            await client.get_issue("psf", "requests", 1234)


@pytest.mark.asyncio
async def test_reject_private_repo() -> None:
    """Verify that private repositories are rejected by safety guard."""
    private_repo_payload = dict(SAMPLE_REPO_PAYLOAD)
    private_repo_payload["private"] = True

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=private_repo_payload)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        with pytest.raises(SafetyViolationError, match="is private"):
            await client.get_repository("psf", "requests")


@pytest.mark.asyncio
async def test_token_authorization_header() -> None:
    """Verify Bearer token is properly attached when configured."""
    captured_auth = None

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal captured_auth
        captured_auth = request.headers.get("authorization")
        return httpx.Response(200, json=SAMPLE_ISSUE_PAYLOAD)

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(token="ghp_fakeSecretToken1234567890", http_client=http)
        await client.get_issue("psf", "requests", 1234)
        assert captured_auth == "Bearer ghp_fakeSecretToken1234567890"
