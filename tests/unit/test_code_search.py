"""Unit tests for deterministic code search and symbol lookup."""

from pathlib import Path
from unittest.mock import patch

from issuelens.repository.code_search import (
    CodeHit,
    extract_search_terms,
    search_code,
    search_symbols,
)
from issuelens.repository.snapshot import RepoSnapshot

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "sample_repo"


def test_extract_search_terms() -> None:
    """Verify query normalization extracts quoted phrases and identifier tokens."""
    query = 'Fix bug in "DatabaseConnectionError" where connect fails with timeout'
    terms = extract_search_terms(query)

    assert "DatabaseConnectionError" in terms
    assert "connect" in terms
    assert "timeout" in terms
    assert "bug" not in terms  # Stop word
    assert "fails" not in terms  # Stop word


def test_search_code_python_fallback() -> None:
    """Verify deterministic pure-Python code search finds matches with exact line numbers."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="sample/repo",
        commit_sha="99887766554433221100aabbccddeeff99887766",
    )

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query="DatabaseConnectionError",
            repo_snapshot=snapshot,
            max_results=5,
        )

        assert len(hits) >= 1
        first_hit = hits[0]
        assert isinstance(first_hit, CodeHit)
        assert first_hit.path == "sample_pkg/database.py"
        assert first_hit.commit_sha == "99887766554433221100aabbccddeeff99887766"
        assert "DatabaseConnectionError" in first_hit.matched_terms
        assert "DatabaseConnectionError" in first_hit.excerpt
        assert first_hit.start_line > 0
        assert first_hit.end_line >= first_hit.start_line


def test_search_code_ast_symbol_expansion() -> None:
    """Verify code search expands line matches to enclosing function/class."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="sample/repo",
        commit_sha="test_sha_123",
    )

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query="validate_token",
            repo_snapshot=snapshot,
            max_results=5,
            expand_symbols=True,
        )

        assert len(hits) >= 1
        auth_hit = next(h for h in hits if h.path == "sample_pkg/auth.py")
        assert auth_hit.symbol_name == "TokenAuthenticator.validate_token"
        assert "def validate_token" in auth_hit.excerpt
        # Range should span the method
        assert auth_hit.end_line > auth_hit.start_line


def test_search_code_excludes_secrets() -> None:
    """Verify secrets (.env) and excluded patterns are never returned."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="sample/repo",
        commit_sha="test_sha_123",
    )

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        # Even if searching for text inside .env
        hits = search_code(
            query="supersecretfixture123",
            repo_snapshot=snapshot,
        )
        assert len(hits) == 0


def test_search_symbols_declaration_lookup() -> None:
    """Verify search_symbols finds classes and functions across repository."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="sample/repo",
        commit_sha="test_sha_123",
    )

    hits = search_symbols(
        symbol_name="ConnectionPool",
        repo_snapshot=snapshot,
    )

    assert len(hits) == 1
    hit = hits[0]
    assert hit.path == "sample_pkg/database.py"
    assert hit.symbol_name == "ConnectionPool"
    assert "class ConnectionPool:" in hit.excerpt


def test_search_code_empty_query() -> None:
    """Verify empty query returns empty results."""
    snapshot = RepoSnapshot(
        root_path=FIXTURE_ROOT,
        repo_full_name="sample/repo",
        commit_sha="test_sha_123",
    )
    assert search_code("", snapshot) == []
    assert search_code("   ", snapshot) == []
