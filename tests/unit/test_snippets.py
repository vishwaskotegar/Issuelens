"""Unit tests for safe line-numbered snippet reader."""

from pathlib import Path

import pytest

from issuelens.core.errors import InvalidInputError
from issuelens.repository.snapshot import RepoSnapshot
from issuelens.repository.snippets import read_snippet

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "sample_repo"


@pytest.fixture
def snapshot() -> RepoSnapshot:
    return RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="sample/repo",
        commit_sha="test_sha_12345",
    )


def test_read_snippet_exact_lines(snapshot: RepoSnapshot) -> None:
    """Verify snippet reads exact lines with 1-indexed line prefixes."""
    # lines 3-7 in sample_pkg/auth.py is class TokenAuthenticator: ...
    snippet = read_snippet(snapshot, "sample_pkg/auth.py", start_line=3, end_line=7)
    lines = snippet.splitlines()
    assert len(lines) == 5
    assert lines[0].startswith("3: class TokenAuthenticator:")
    assert "def __init__" in snippet


def test_read_snippet_with_context(snapshot: RepoSnapshot) -> None:
    """Verify context lines expand window safely."""
    snippet = read_snippet(
        snapshot, "sample_pkg/auth.py", start_line=5, end_line=5, context_lines=2
    )
    lines = snippet.splitlines()
    assert len(lines) == 5
    assert lines[0].startswith("3: ")
    assert lines[2].startswith("5: ")
    assert lines[4].startswith("7: ")


def test_read_snippet_invalid_start_line(snapshot: RepoSnapshot) -> None:
    """Verify 0 or negative start line raises InvalidInputError."""
    with pytest.raises(InvalidInputError, match="Start line must be >= 1"):
        read_snippet(snapshot, "sample_pkg/auth.py", start_line=0, end_line=5)


def test_read_snippet_end_line_before_start_line(snapshot: RepoSnapshot) -> None:
    """Verify end_line < start_line raises InvalidInputError."""
    with pytest.raises(InvalidInputError, match="cannot be less than start line"):
        read_snippet(snapshot, "sample_pkg/auth.py", start_line=10, end_line=5)


def test_read_snippet_clamped_to_file_boundaries(snapshot: RepoSnapshot) -> None:
    """Verify requests past file end clamp to actual line count."""
    snippet = read_snippet(snapshot, "sample_pkg/__init__.py", start_line=1, end_line=100)
    lines = snippet.splitlines()
    assert len(lines) <= 5
    assert lines[0].startswith("1: ")
