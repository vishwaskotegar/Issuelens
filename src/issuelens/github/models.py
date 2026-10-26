"""Domain and Pydantic models for GitHub issues, comments, and repository metadata."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class GitHubComment(BaseModel):
    """A single comment on a GitHub issue."""

    id: int = Field(..., description="GitHub comment ID")
    author: str | None = Field(default=None, description="Username of comment author")
    body: str = Field(default="", description="Untrusted comment body markdown/text")
    created_at: datetime = Field(..., description="Timestamp when comment was created")
    updated_at: datetime | None = Field(default=None, description="Timestamp of last update")
    html_url: str | None = Field(default=None, description="URL to the comment on GitHub")


class GitHubIssue(BaseModel):
    """Parsed issue metadata and content from GitHub REST API."""

    number: int = Field(..., description="Issue number")
    title: str = Field(..., description="Issue title")
    body: str = Field(default="", description="Untrusted issue body markdown/text")
    state: str = Field(default="open", description="Issue state: open or closed")
    author: str | None = Field(default=None, description="Username of issue creator")
    labels: list[str] = Field(default_factory=list, description="Associated issue label names")
    created_at: datetime = Field(..., description="Timestamp when issue was created")
    updated_at: datetime | None = Field(default=None, description="Timestamp of last update")
    closed_at: datetime | None = Field(default=None, description="Timestamp when issue was closed")
    comments_count: int = Field(default=0, description="Total number of comments")
    html_url: str = Field(..., description="Canonical HTML URL to issue")
    is_pull_request: bool = Field(
        default=False, description="Flag indicating if item is a pull request"
    )

    @classmethod
    def from_api_payload(cls, data: dict[str, Any]) -> "GitHubIssue":
        """Instantiate from raw GitHub REST API issue payload."""
        user = data.get("user")
        author = user.get("login") if isinstance(user, dict) else None
        raw_labels = data.get("labels", [])
        label_names = [
            label.get("name", "") if isinstance(label, dict) else str(label)
            for label in raw_labels
            if label
        ]

        return cls(
            number=data["number"],
            title=data.get("title", ""),
            body=data.get("body") or "",
            state=data.get("state", "open"),
            author=author,
            labels=[lbl for lbl in label_names if lbl],
            created_at=data["created_at"],
            updated_at=data.get("updated_at"),
            closed_at=data.get("closed_at"),
            comments_count=data.get("comments", 0),
            html_url=data.get("html_url", ""),
            is_pull_request="pull_request" in data,
        )


class GitHubRepo(BaseModel):
    """Metadata about the parent repository."""

    owner: str = Field(..., description="Repository owner/org name")
    name: str = Field(..., description="Repository name")
    full_name: str = Field(..., description="Full repository name: owner/repo")
    default_branch: str = Field(default="main", description="Default branch name")
    is_private: bool = Field(default=False, description="Whether repository is private")
    html_url: str = Field(..., description="Canonical URL to repository")
    description: str | None = Field(default=None, description="Repository description")

    @classmethod
    def from_api_payload(cls, data: dict[str, Any]) -> "GitHubRepo":
        """Instantiate from raw GitHub REST API repository payload."""
        owner_data = data.get("owner", {})
        owner_name = owner_data.get("login") if isinstance(owner_data, dict) else ""
        return cls(
            owner=owner_name or data.get("full_name", "").split("/")[0],
            name=data.get("name", ""),
            full_name=data.get("full_name", ""),
            default_branch=data.get("default_branch", "main"),
            is_private=data.get("private", False),
            html_url=data.get("html_url", ""),
            description=data.get("description"),
        )


class GitHubIssueContext(BaseModel):
    """Aggregated issue investigation context retrieved from GitHub."""

    issue_url: str = Field(..., description="Canonical issue URL")
    repository: str = Field(..., description="Repository full name: owner/repo")
    issue_number: int = Field(..., description="Issue number")
    default_branch: str = Field(..., description="Default branch name")
    commit_sha: str = Field(..., description="Pinned commit SHA representing snapshot")
    issue: GitHubIssue = Field(..., description="Core issue metadata and description")
    comments: list[GitHubComment] = Field(
        default_factory=list, description="List of issue comments"
    )
    fetched_at: datetime = Field(..., description="Timestamp when data was retrieved")
