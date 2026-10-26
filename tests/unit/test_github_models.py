"""Unit tests for GitHub domain models."""

from datetime import UTC, datetime

from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
    GitHubRepo,
)


def test_github_issue_from_payload() -> None:
    """Verify issue model parses raw GitHub API responses with optional/null fields."""
    payload = {
        "number": 42,
        "title": "Fix memory leak",
        "body": None,  # Body can be null in GitHub API
        "state": "closed",
        "user": None,  # Deleted user
        "labels": [{"name": "performance"}, "string-label", None],
        "created_at": "2026-10-01T00:00:00Z",
        "updated_at": None,
        "closed_at": "2026-10-02T00:00:00Z",
        "comments": 5,
        "html_url": "https://github.com/org/repo/issues/42",
    }

    issue = GitHubIssue.from_api_payload(payload)
    assert issue.number == 42
    assert issue.title == "Fix memory leak"
    assert issue.body == ""  # Normalized from None
    assert issue.state == "closed"
    assert issue.author is None
    assert issue.labels == ["performance", "string-label"]
    assert issue.comments_count == 5
    assert not issue.is_pull_request


def test_github_repo_from_payload() -> None:
    """Verify repository model deserialization."""
    payload = {
        "name": "issuelens",
        "full_name": "org/issuelens",
        "owner": {"login": "org"},
        "default_branch": "master",
        "private": False,
        "html_url": "https://github.com/org/issuelens",
        "description": "Evidence-backed debugging",
    }
    repo = GitHubRepo.from_api_payload(payload)
    assert repo.owner == "org"
    assert repo.name == "issuelens"
    assert repo.default_branch == "master"
    assert not repo.is_private


def test_github_issue_context_serialization() -> None:
    """Verify GitHubIssueContext serialization and properties."""
    issue = GitHubIssue(
        number=1,
        title="Test issue",
        body="details",
        created_at=datetime.now(tz=UTC),
        html_url="https://github.com/a/b/issues/1",
    )
    comment = GitHubComment(
        id=10,
        author="alice",
        body="looks good",
        created_at=datetime.now(tz=UTC),
    )
    context = GitHubIssueContext(
        issue_url="https://github.com/a/b/issues/1",
        repository="a/b",
        issue_number=1,
        default_branch="main",
        commit_sha="1234567890abcdef1234567890abcdef12345678",
        issue=issue,
        comments=[comment],
        fetched_at=datetime.now(tz=UTC),
    )
    dumped = context.model_dump()
    assert dumped["issue_number"] == 1
    assert dumped["commit_sha"] == "1234567890abcdef1234567890abcdef12345678"
    assert len(dumped["comments"]) == 1
