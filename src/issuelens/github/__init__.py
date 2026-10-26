"""GitHub integration package for IssueLens."""

from issuelens.github.client import GitHubClient, GitHubClientProtocol, get_github_client
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
    GitHubRepo,
)
from issuelens.github.url_parser import IssueTarget, parse_issue_target

__all__ = [
    "GitHubClient",
    "GitHubClientProtocol",
    "GitHubComment",
    "GitHubIssue",
    "GitHubIssueContext",
    "GitHubRepo",
    "IssueTarget",
    "get_github_client",
    "parse_issue_target",
]
