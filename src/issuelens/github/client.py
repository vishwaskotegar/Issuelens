"""GitHub REST API client adapter with rate limiting, timeouts, and error mapping."""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from pydantic import SecretStr

from issuelens import __version__
from issuelens.core.errors import (
    InvalidInputError,
    IssueLensError,
    RateLimitExceededError,
    ResourceNotFoundError,
    SafetyViolationError,
    UpstreamTimeoutError,
)
from issuelens.core.logging import get_logger
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
    GitHubRepo,
)
from issuelens.github.url_parser import IssueTarget

logger = get_logger("issuelens.github.client")


class GitHubClientProtocol(Protocol):
    """Protocol interface for GitHub API clients enabling dependency injection."""

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        """Fetch issue metadata from repository."""
        ...

    async def get_comments(
        self, owner: str, repo: str, issue_number: int, max_comments: int | None = None
    ) -> list[GitHubComment]:
        """Fetch comments for a given issue."""
        ...

    async def get_repository(self, owner: str, repo: str) -> GitHubRepo:
        """Fetch repository metadata and default branch."""
        ...

    async def get_pinned_commit_sha(self, owner: str, repo: str, ref: str) -> str:
        """Resolve ref (branch or tag) to a 40-character commit SHA."""
        ...

    async def fetch_issue_context(self, target: IssueTarget) -> GitHubIssueContext:
        """Orchestrate fetching of issue, comments, repo, and pinned commit SHA."""
        ...


class GitHubClient:
    """HTTP client adapter for the GitHub REST API (v3)."""

    def __init__(
        self,
        token: str | SecretStr | None = None,
        timeout_seconds: float = 15.0,
        base_url: str = "https://api.github.com",
        max_comments: int = 100,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_comments = max_comments
        self._external_client = http_client

        raw_token = token.get_secret_value() if isinstance(token, SecretStr) else token
        self._headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "User-Agent": f"IssueLens/{__version__}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if raw_token:
            self._headers["Authorization"] = f"Bearer {raw_token}"

        self._client: httpx.AsyncClient | None = http_client

    async def __aenter__(self) -> "GitHubClient":
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers=self._headers,
                timeout=self.timeout_seconds,
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> None:
        if self._external_client is None and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _get_active_client(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        # If client not entered as context manager and no external client supplied
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers=self._headers,
            timeout=self.timeout_seconds,
        )
        return self._client

    async def close(self) -> None:
        """Close internal HTTP client if owned."""
        if self._external_client is None and self._client is not None:
            await self._client.aclose()
            self._client = None

    def _handle_error_response(self, response: httpx.Response, resource: str) -> None:
        """Inspect HTTP response and raise appropriate domain error."""
        status_code = response.status_code

        # Rate limiting: 429 or 403 with x-ratelimit-remaining == '0'
        remaining = response.headers.get("x-ratelimit-remaining")
        reset_time = response.headers.get("x-ratelimit-reset")
        retry_after = response.headers.get("retry-after")

        is_rate_limited = status_code == 429 or (
            status_code == 403
            and (
                remaining == "0"
                or "rate limit" in response.text.lower()
                or "secondary rate limit" in response.text.lower()
            )
        )

        if is_rate_limited:
            reset_msg = ""
            if reset_time:
                try:
                    dt = datetime.fromtimestamp(int(reset_time), tz=UTC)
                    reset_msg = f" Rate limit resets at {dt.isoformat()}."
                except (ValueError, TypeError):
                    pass
            elif retry_after:
                reset_msg = f" Retry after {retry_after} seconds."

            raise RateLimitExceededError(
                f"GitHub API rate limit exceeded when requesting {resource}.{reset_msg}"
            )

        if status_code == 404:
            raise ResourceNotFoundError(
                f"GitHub resource '{resource}' not found (HTTP 404)."
            )

        if status_code == 401:
            raise SafetyViolationError(
                "GitHub authentication failed. Please verify your GITHUB_TOKEN."
            )

        if status_code == 403:
            raise SafetyViolationError(
                f"Access to GitHub resource '{resource}' is forbidden (HTTP 403)."
            )

        if status_code >= 500:
            raise IssueLensError(
                f"GitHub API returned upstream server error {status_code} for {resource}.",
                code="UPSTREAM_SERVER_ERROR",
                status_code=502,
            )

        raise IssueLensError(
            f"GitHub API returned error {status_code} for {resource}: {response.text[:200]}",
            code="GITHUB_API_ERROR",
            status_code=status_code,
        )

    async def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        """Execute HTTP request with error handling and timeout wrapping."""
        client = self._get_active_client()
        url = path if path.startswith("http") else f"{self.base_url}{path}"

        headers = dict(self._headers)
        if "headers" in kwargs:
            headers.update(kwargs.pop("headers"))

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                response = await client.request(method, url, headers=headers, **kwargs)
                if response.is_success:
                    return response.json()

                # If transient 5xx, retry once
                if response.status_code in (500, 502, 503, 504) and attempt < max_retries:
                    logger.warning(
                        "GitHub API returned %d for %s; retrying attempt %d",
                        response.status_code,
                        path,
                        attempt + 1,
                    )
                    continue

                self._handle_error_response(response, path)

            except httpx.TimeoutException as exc:
                if attempt < max_retries:
                    continue
                raise UpstreamTimeoutError(
                    f"GitHub request to '{path}' timed out after {self.timeout_seconds}s."
                ) from exc
            except httpx.RequestError as exc:
                if attempt < max_retries:
                    continue
                raise IssueLensError(
                    f"GitHub network communication error for '{path}': {exc}",
                    code="NETWORK_ERROR",
                    status_code=502,
                ) from exc

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        """Fetch issue metadata from repository."""
        path = f"/repos/{owner}/{repo}/issues/{issue_number}"
        data = await self._request("GET", path)

        if "pull_request" in data:
            raise InvalidInputError(
                f"Resource '{owner}/{repo}#{issue_number}' is a pull request, not an issue. "
                "IssueLens only investigates bug report issues."
            )

        return GitHubIssue.from_api_payload(data)

    async def get_comments(
        self, owner: str, repo: str, issue_number: int, max_comments: int | None = None
    ) -> list[GitHubComment]:
        """Fetch comments for an issue up to limit."""
        limit = max_comments if max_comments is not None else self.max_comments
        path = f"/repos/{owner}/{repo}/issues/{issue_number}/comments"
        params = {"per_page": min(limit, 100)}
        data = await self._request("GET", path, params=params)

        comments: list[GitHubComment] = []
        if isinstance(data, list):
            for item in data[:limit]:
                user = item.get("user")
                author = user.get("login") if isinstance(user, dict) else None
                comments.append(
                    GitHubComment(
                        id=item["id"],
                        author=author,
                        body=item.get("body") or "",
                        created_at=item["created_at"],
                        updated_at=item.get("updated_at"),
                        html_url=item.get("html_url"),
                    )
                )
        return comments

    async def get_repository(self, owner: str, repo: str) -> GitHubRepo:
        """Fetch repository metadata and default branch."""
        path = f"/repos/{owner}/{repo}"
        data = await self._request("GET", path)
        repo_model = GitHubRepo.from_api_payload(data)

        if repo_model.is_private:
            raise SafetyViolationError(
                f"Repository '{owner}/{repo}' is private. "
                "IssueLens only inspects public repositories."
            )

        return repo_model

    async def get_pinned_commit_sha(self, owner: str, repo: str, ref: str) -> str:
        """Resolve ref (branch, tag, or SHA) to a verified 40-character commit SHA."""
        path = f"/repos/{owner}/{repo}/commits/{ref}"
        data = await self._request("GET", path)
        sha = data.get("sha")
        if not sha or not isinstance(sha, str):
            raise IssueLensError(
                f"Failed to resolve commit SHA for '{owner}/{repo}@{ref}'.",
                code="COMMIT_RESOLUTION_FAILED",
            )
        return sha

    async def fetch_issue_context(self, target: IssueTarget) -> GitHubIssueContext:
        """Fetch and aggregate issue, comments, repo metadata, and pinned commit SHA."""
        logger.info(
            "Ingesting GitHub issue context for %s#%d",
            target.repo_full_name,
            target.issue_number,
        )

        # 1. Fetch Issue
        issue = await self.get_issue(target.owner, target.repo, target.issue_number)

        # 2. Fetch Comments (bounded by max_comments)
        comments: list[GitHubComment] = []
        if issue.comments_count > 0:
            comments = await self.get_comments(
                target.owner, target.repo, target.issue_number, self.max_comments
            )

        # 3. Fetch Repository to discover default branch and ensure public
        repository = await self.get_repository(target.owner, target.repo)

        # 4. Resolve default branch head to pinned commit SHA
        commit_sha = await self.get_pinned_commit_sha(
            target.owner, target.repo, repository.default_branch
        )

        return GitHubIssueContext(
            issue_url=target.canonical_url,
            repository=target.repo_full_name,
            issue_number=target.issue_number,
            default_branch=repository.default_branch,
            commit_sha=commit_sha,
            issue=issue,
            comments=comments,
            fetched_at=datetime.now(tz=UTC),
        )


@asynccontextmanager
async def get_github_client(
    token: str | SecretStr | None = None,
    timeout_seconds: float = 15.0,
    max_comments: int = 100,
) -> AsyncGenerator[GitHubClient, None]:
    """Dependency helper yielding a managed GitHubClient."""
    client = GitHubClient(
        token=token,
        timeout_seconds=timeout_seconds,
        max_comments=max_comments,
    )
    async with client as active:
        yield active
