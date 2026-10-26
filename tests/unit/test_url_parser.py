"""Unit tests for GitHub issue URL and coordinate parser."""

import pytest

from issuelens.core.errors import InvalidInputError
from issuelens.github.url_parser import IssueTarget, parse_issue_target


def test_parse_valid_https_url() -> None:
    """Test parsing standard public GitHub issue HTTPS URLs."""
    url = "https://github.com/psf/requests/issues/1234"
    target = parse_issue_target(url)
    assert target == IssueTarget(owner="psf", repo="requests", issue_number=1234)
    assert target.repo_full_name == "psf/requests"
    assert target.canonical_url == "https://github.com/psf/requests/issues/1234"


def test_parse_valid_url_with_trailing_slash() -> None:
    """Test parsing URL with trailing slash."""
    url = "https://github.com/encode/fastapi/issues/42/"
    target = parse_issue_target(url)
    assert target == IssueTarget(owner="encode", repo="fastapi", issue_number=42)


def test_parse_valid_url_with_query_and_fragment() -> None:
    """Test parsing URL with query params and comment hash fragments."""
    url = "https://github.com/pydantic/pydantic/issues/999?utm_source=slack#issuecomment-555"
    target = parse_issue_target(url)
    assert target == IssueTarget(owner="pydantic", repo="pydantic", issue_number=999)


def test_parse_valid_http_scheme() -> None:
    """Test parsing plain HTTP GitHub URL normalizes to canonical HTTPS."""
    url = "http://github.com/octocat/Hello-World/issues/1"
    target = parse_issue_target(url)
    assert target == IssueTarget(owner="octocat", repo="Hello-World", issue_number=1)
    assert target.canonical_url == "https://github.com/octocat/Hello-World/issues/1"


def test_parse_valid_coordinates_hash() -> None:
    """Test parsing coordinate format: owner/repo#number."""
    target = parse_issue_target("langchain-ai/langgraph#500")
    assert target == IssueTarget(owner="langchain-ai", repo="langgraph", issue_number=500)


def test_parse_valid_coordinates_colon() -> None:
    """Test parsing coordinate format: owner/repo:number."""
    target = parse_issue_target("astral-sh/uv:200")
    assert target == IssueTarget(owner="astral-sh", repo="uv", issue_number=200)


def test_parse_valid_coordinates_path() -> None:
    """Test parsing coordinate format: owner/repo/issues/number."""
    target = parse_issue_target("pallets/flask/issues/300")
    assert target == IssueTarget(owner="pallets", repo="flask", issue_number=300)


def test_reject_unsupported_host_ssrf() -> None:
    """Verify non-github hosts are rejected to prevent SSRF."""
    with pytest.raises(InvalidInputError, match="Unsupported host 'gitlab.com'"):
        parse_issue_target("https://gitlab.com/owner/repo/issues/123")

    with pytest.raises(InvalidInputError, match="Unsupported host 'evil.com'"):
        parse_issue_target("https://evil.com/owner/repo/issues/123")

    with pytest.raises(InvalidInputError, match="Unsupported host 'localhost'"):
        parse_issue_target("http://localhost/owner/repo/issues/123")


def test_reject_unsupported_scheme() -> None:
    """Verify non-http/https schemes are rejected."""
    with pytest.raises(InvalidInputError, match="Unsupported URL scheme 'ftp'"):
        parse_issue_target("ftp://github.com/owner/repo/issues/123")

    with pytest.raises(InvalidInputError, match="Unsupported URL scheme 'file'"):
        parse_issue_target("file://github.com/owner/repo/issues/123")


def test_reject_pull_request_urls() -> None:
    """Verify pull request URLs are clearly rejected."""
    with pytest.raises(InvalidInputError, match="targets a pull request, not an issue"):
        parse_issue_target("https://github.com/psf/requests/pull/1234")


def test_reject_malformed_url_paths() -> None:
    """Verify incomplete or wrong GitHub paths are rejected."""
    with pytest.raises(InvalidInputError, match="Invalid GitHub issue path"):
        parse_issue_target("https://github.com/psf/requests")

    with pytest.raises(InvalidInputError, match="Invalid GitHub issue path"):
        parse_issue_target("https://github.com/psf/requests/tree/main")


def test_reject_invalid_issue_number() -> None:
    """Verify non-positive or non-integer issue numbers are rejected."""
    with pytest.raises(InvalidInputError, match="Issue number must be a valid integer"):
        parse_issue_target("https://github.com/psf/requests/issues/notanumber")

    with pytest.raises(InvalidInputError, match="Issue number must be a positive integer"):
        parse_issue_target("https://github.com/psf/requests/issues/0")

    with pytest.raises(InvalidInputError, match="Issue number must be a positive integer"):
        parse_issue_target("https://github.com/psf/requests/issues/-5")


def test_reject_empty_or_whitespace() -> None:
    """Verify empty input is rejected."""
    with pytest.raises(InvalidInputError, match="Issue URL or coordinates cannot be empty"):
        parse_issue_target("")

    with pytest.raises(InvalidInputError, match="Issue URL or coordinates cannot be empty"):
        parse_issue_target("   ")


def test_reject_path_traversal_attempts() -> None:
    """Verify path traversal characters are blocked."""
    with pytest.raises(InvalidInputError):
        parse_issue_target("https://github.com/../evil/issues/1")

    with pytest.raises(InvalidInputError):
        parse_issue_target("https://github.com/owner/../issues/1")
