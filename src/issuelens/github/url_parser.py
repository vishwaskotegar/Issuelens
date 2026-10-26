"""GitHub issue URL and coordinate parser with strict validation and SSRF prevention."""

import re
from dataclasses import dataclass
from urllib.parse import urlparse

from issuelens.core.errors import InvalidInputError

# GitHub username: 1-39 chars, alphanumeric and single hyphens
GITHUB_OWNER_REGEX = re.compile(r"^[a-zA-Z0-9](?:[a-zA-Z0-9]|-(?=[a-zA-Z0-9])){0,38}$")

# GitHub repo name: 1-100 chars, alphanumeric, hyphens, underscores, dots (cannot be . or ..)
GITHUB_REPO_REGEX = re.compile(r"^[a-zA-Z0-9_.-]{1,100}$")

# Coordinate patterns: owner/repo#number or owner/repo/issues/number or owner/repo:number
COORDINATE_REGEX = re.compile(
    r"^(?P<owner>[a-zA-Z0-9_.-]+)/(?P<repo>[a-zA-Z0-9_.-]+)(?:#|:|(?:/issues/))(?P<number>\d+)$"
)


@dataclass(frozen=True)
class IssueTarget:
    """Parsed and validated target coordinates for a GitHub issue."""

    owner: str
    repo: str
    issue_number: int

    @property
    def repo_full_name(self) -> str:
        """Formatted repository full name: 'owner/repo'."""
        return f"{self.owner}/{self.repo}"

    @property
    def canonical_url(self) -> str:
        """Canonical HTTPS issue URL."""
        return f"https://github.com/{self.owner}/{self.repo}/issues/{self.issue_number}"


def _validate_owner_and_repo(owner: str, repo: str) -> tuple[str, str]:
    """Validate and normalize owner and repository names."""
    clean_owner = owner.strip()
    clean_repo = repo.strip()

    if clean_repo.endswith(".git"):
        clean_repo = clean_repo[:-4]

    if not GITHUB_OWNER_REGEX.match(clean_owner):
        raise InvalidInputError(
            f"Invalid GitHub owner/organization identifier: '{clean_owner}'."
        )

    if not GITHUB_REPO_REGEX.match(clean_repo) or clean_repo in {".", ".."}:
        raise InvalidInputError(
            f"Invalid GitHub repository identifier: '{clean_repo}'."
        )

    return clean_owner, clean_repo


def _validate_issue_number(issue_number_raw: str | int) -> int:
    """Validate that issue number is a positive integer."""
    try:
        number = int(issue_number_raw)
    except (ValueError, TypeError) as exc:
        raise InvalidInputError(
            f"Issue number must be a valid integer, got '{issue_number_raw}'."
        ) from exc

    if number <= 0:
        raise InvalidInputError(
            f"Issue number must be a positive integer > 0, got {number}."
        )

    return number


def parse_issue_target(raw_input: str) -> IssueTarget:
    """Parse and strictly validate a GitHub issue URL or coordinate string.

    Supported inputs:
    - https://github.com/owner/repo/issues/123
    - http://github.com/owner/repo/issues/123
    - owner/repo#123
    - owner/repo:123
    - owner/repo/issues/123

    Raises:
        InvalidInputError: If the input is malformed, uses an unsupported host,
                           targets a pull request, or has invalid coordinates.
    """
    if not raw_input or not isinstance(raw_input, str) or not raw_input.strip():
        raise InvalidInputError("Issue URL or coordinates cannot be empty.")

    cleaned = raw_input.strip()

    # If input contains scheme or begins with http:// or https:// or //
    if "://" in cleaned or cleaned.startswith("//"):
        parsed = urlparse(cleaned)

        if parsed.scheme and parsed.scheme not in ("http", "https"):
            raise InvalidInputError(
                f"Unsupported URL scheme '{parsed.scheme}'. Only HTTPS is permitted."
            )

        hostname = (parsed.hostname or "").lower()
        if hostname not in ("github.com", "www.github.com"):
            raise InvalidInputError(
                f"Unsupported host '{hostname}'. "
                "IssueLens only supports public GitHub repositories (github.com)."
            )

        # Path format expected: /{owner}/{repo}/issues/{number}
        # Filter out empty parts
        path_parts = [p for p in parsed.path.strip("/").split("/") if p]

        # Check for pull request attempt
        if len(path_parts) >= 3 and path_parts[2] == "pull":
            raise InvalidInputError(
                f"Provided URL targets a pull request, not an issue: '{cleaned}'. "
                "IssueLens requires an issue URL (/issues/<number>)."
            )

        if len(path_parts) < 4 or path_parts[2] != "issues":
            raise InvalidInputError(
                f"Invalid GitHub issue path '{parsed.path}'. "
                "Expected format: https://github.com/<owner>/<repo>/issues/<number>"
            )

        owner, repo = _validate_owner_and_repo(path_parts[0], path_parts[1])
        number = _validate_issue_number(path_parts[3])
        return IssueTarget(owner=owner, repo=repo, issue_number=number)

    # Coordinate string format (e.g. owner/repo#123 or owner/repo:123 or owner/repo/issues/123)
    match = COORDINATE_REGEX.match(cleaned)
    if match:
        owner, repo = _validate_owner_and_repo(match.group("owner"), match.group("repo"))
        number = _validate_issue_number(match.group("number"))
        return IssueTarget(owner=owner, repo=repo, issue_number=number)

    raise InvalidInputError(
        f"Unable to parse issue identifier: '{cleaned}'. "
        "Expected https://github.com/<owner>/<repo>/issues/<number> or <owner>/<repo>#<number>."
    )
