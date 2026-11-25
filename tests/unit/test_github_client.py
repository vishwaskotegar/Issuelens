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
from issuelens.github.client import GitHubClient, extract_candidate_versions
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


@pytest.mark.asyncio
async def test_get_issue_context_resolves_version_tag_from_title() -> None:
    """Verify that semver version in title resolves to pinned release tag commit SHA."""
    issue_payload = dict(SAMPLE_ISSUE_PAYLOAD)
    issue_payload["title"] = "Bug in v8.0.0 when handling maxfail"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/psf/requests/issues/1234":
            return httpx.Response(200, json=issue_payload)
        if path == "/repos/psf/requests/issues/1234/comments":
            return httpx.Response(200, json=[])
        if path == "/repos/psf/requests":
            return httpx.Response(200, json=SAMPLE_REPO_PAYLOAD)
        if path == "/repos/psf/requests/commits/v8.0.0":
            return httpx.Response(200, json={"sha": "8888888888888888888888888888888888888888"})
        if path == "/repos/psf/requests/commits/main":
            return httpx.Response(200, json=SAMPLE_COMMIT_PAYLOAD)
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        target = IssueTarget(owner="psf", repo="requests", issue_number=1234)
        context = await client.fetch_issue_context(target)

        assert context.commit_sha == "8888888888888888888888888888888888888888"


@pytest.mark.asyncio
async def test_get_issue_context_falls_back_to_default_branch() -> None:
    """Verify that if version tag 404s, client falls back to repository default branch."""
    issue_payload = dict(SAMPLE_ISSUE_PAYLOAD)
    issue_payload["title"] = "Bug in v9.9.9 unreleased version"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/psf/requests/issues/1234":
            return httpx.Response(200, json=issue_payload)
        if path == "/repos/psf/requests/issues/1234/comments":
            return httpx.Response(200, json=[])
        if path == "/repos/psf/requests":
            return httpx.Response(200, json=SAMPLE_REPO_PAYLOAD)
        if path in ("/repos/psf/requests/commits/v9.9.9", "/repos/psf/requests/commits/9.9.9"):
            return httpx.Response(404, json={"message": "Not Found"})
        if path == "/repos/psf/requests/commits/main":
            return httpx.Response(200, json=SAMPLE_COMMIT_PAYLOAD)
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        target = IssueTarget(owner="psf", repo="requests", issue_number=1234)
        context = await client.fetch_issue_context(target)

        assert context.commit_sha == "abcdef1234567890abcdef1234567890abcdef12"


def test_extract_candidate_versions_expands_two_digit_semver() -> None:
    """Verify that two-digit version strings (0.106) expand to three-digit candidate (0.106.0)."""
    versions = extract_candidate_versions(
        "Context managers in Depends are broken after 0.106",
        ["release: 1.2"],
    )
    assert "0.106" in versions
    assert "0.106.0" in versions
    assert "1.2" in versions
    assert "1.2.0" in versions


@pytest.mark.asyncio
async def test_get_issue_context_resolves_two_digit_version_tag() -> None:
    """Verify that two-digit version in title probes and resolves tag 0.106.0 on 404 for 0.106."""
    issue_payload = dict(SAMPLE_ISSUE_PAYLOAD)
    issue_payload["title"] = "Context managers in `Depends` are broken after 0.106"

    fastapi_repo_payload = dict(SAMPLE_REPO_PAYLOAD)
    fastapi_repo_payload["full_name"] = "fastapi/fastapi"
    fastapi_repo_payload["name"] = "fastapi"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/fastapi/fastapi/issues/11143":
            return httpx.Response(200, json=issue_payload)
        if path == "/repos/fastapi/fastapi/issues/11143/comments":
            return httpx.Response(200, json=[])
        if path == "/repos/fastapi/fastapi":
            return httpx.Response(200, json=fastapi_repo_payload)
        # v0.106 and 0.106 return 404
        if path in (
            "/repos/fastapi/fastapi/commits/v0.106",
            "/repos/fastapi/fastapi/commits/0.106",
        ):
            return httpx.Response(404, json={"message": "Not Found"})
        # 0.106.0 exists
        if path == "/repos/fastapi/fastapi/commits/0.106.0":
            return httpx.Response(200, json={"sha": "a1b3be01aadc61c67edc09eed4f6cc1fcc3d3b8f"})
        if path == "/repos/fastapi/fastapi/commits/main":
            return httpx.Response(200, json=SAMPLE_COMMIT_PAYLOAD)
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        target = IssueTarget(owner="fastapi", repo="fastapi", issue_number=11143)
        context = await client.fetch_issue_context(target)

        assert context.commit_sha == "a1b3be01aadc61c67edc09eed4f6cc1fcc3d3b8f"




@pytest.mark.asyncio
async def test_archive_download_error_body_is_read_before_classification() -> None:
    """Regression: a streamed 403/422 archive response raised httpx.ResponseNotRead.

    The error classifier reads ``response.text``; inside ``client.stream()`` the body has
    not been read yet, so classification crashed instead of raising a domain error.
    """

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, stream=httpx.ByteStream(b'{"message": "Forbidden"}'))

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        with pytest.raises(SafetyViolationError, match="forbidden"):
            await client.download_archive("owner", "repo", "abc123")


@pytest.mark.asyncio
async def test_version_tag_lookup_does_not_swallow_rate_limits() -> None:
    """Regression: a rate-limited tag lookup silently fell back to the default branch."""
    issue_payload = dict(SAMPLE_ISSUE_PAYLOAD)
    issue_payload["title"] = "Bug in v8.0.0 when handling maxfail"

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/repos/psf/requests/issues/1234":
            return httpx.Response(200, json=issue_payload)
        if path == "/repos/psf/requests/issues/1234/comments":
            return httpx.Response(200, json=[])
        if path == "/repos/psf/requests":
            return httpx.Response(200, json=SAMPLE_REPO_PAYLOAD)
        if path.startswith("/repos/psf/requests/commits/v8"):
            return httpx.Response(429, json={"message": "rate limited"})
        if path == "/repos/psf/requests/commits/main":
            return httpx.Response(200, json=SAMPLE_COMMIT_PAYLOAD)
        return httpx.Response(404, json={"message": "Not Found"})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as http:
        client = GitHubClient(http_client=http)
        target = IssueTarget(owner="psf", repo="requests", issue_number=1234)
        with pytest.raises(RateLimitExceededError):
            await client.fetch_issue_context(target)
