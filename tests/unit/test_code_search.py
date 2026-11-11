"""Unit tests for deterministic code search and symbol lookup."""

from pathlib import Path
from unittest.mock import patch

from issuelens.repository.code_search import (
    CodeHit,
    classify_path,
    extract_search_terms,
    search_code,
    search_symbols,
    select_search_terms,
)
from issuelens.repository.snapshot import RepoSnapshot
from issuelens.repository.snippets import read_snippet

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


def test_extract_search_terms_preserves_paths() -> None:
    """Verify repository-relative paths stay intact as a single search term."""
    terms = extract_search_terms("Crash while importing requests/models.py at startup")
    assert "requests/models.py" in terms


def test_extract_search_terms_expands_qualified_symbols() -> None:
    """Verify dotted qualified names expose their final class/function name."""
    terms = extract_search_terms("requests.exceptions.ProxyError cannot connect")
    assert "requests.exceptions.ProxyError" in terms
    assert "ProxyError" in terms


def test_select_search_terms_drops_prose_when_identifiers_exist() -> None:
    """Verify natural-language words are dropped when enough identifiers are present."""
    selected = dict(
        select_search_terms(
            "MySpecialError raised when downloading large body "
            "requests/models.py iter_content IncompleteRead"
        )
    )
    assert "MySpecialError" in selected
    assert "requests/models.py" in selected
    assert "iter_content" in selected
    assert "when" not in selected
    assert "large" not in selected


def test_classify_path_separates_code_and_documentation() -> None:
    """Verify source files and prose/changelog files are classified distinctly."""
    assert classify_path("src/requests/models.py") == "code"
    assert classify_path("HISTORY.md") == "documentation"
    assert classify_path("docs/api.rst") == "documentation"
    assert classify_path("tests/test_requests.py") == "code"


def _build_ranking_repo(tmp_path: Path) -> RepoSnapshot:
    """Create a small repository where a changelog floods the error identifier."""
    changelog_lines = [
        f"-   Fixed ChunkedEncodingError handling regression number {i}." for i in range(30)
    ]
    (tmp_path / "HISTORY.md").write_text("\n".join(changelog_lines) + "\n", encoding="utf-8")

    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "exceptions.py").write_text(
        "class ChunkedEncodingError(RequestException):\n"
        '    """Raised when a chunked response is invalid."""\n'
        "\n"
        "\n"
        "def iter_content(stream):\n"
        "    if not stream:\n"
        "        raise ChunkedEncodingError('broken stream')\n"
        "    return stream\n",
        encoding="utf-8",
    )
    return RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="sample/ranking",
        commit_sha="abc123",
    )


def test_search_code_ranks_source_code_above_changelog(tmp_path: Path) -> None:
    """Verify a changelog full of matches cannot crowd out the defining source file."""
    snapshot = _build_ranking_repo(tmp_path)

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query=(
                "ChunkedEncodingError raised when downloading a large response body "
                "from iter_content; see src/pkg/exceptions.py"
            ),
            repo_snapshot=snapshot,
            max_results=5,
        )

    assert hits
    assert hits[0].path.startswith("src/")
    assert "ChunkedEncodingError" in hits[0].excerpt
    # Every source hit must appear before the changelog hit.
    source_positions = [i for i, hit in enumerate(hits) if hit.path.startswith("src/")]
    doc_positions = [i for i, hit in enumerate(hits) if hit.path == "HISTORY.md"]
    assert source_positions
    if doc_positions:
        assert max(source_positions) < min(doc_positions)


def test_search_code_excerpt_matches_declared_range(tmp_path: Path) -> None:
    """Verify every excerpt covers exactly the declared start_line..end_line window."""
    snapshot = _build_ranking_repo(tmp_path)

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query="ChunkedEncodingError iter_content src/pkg/exceptions.py",
            repo_snapshot=snapshot,
            max_results=10,
        )

    assert hits
    for hit in hits:
        physical = read_snippet(
            snapshot,
            hit.path,
            start_line=hit.start_line,
            end_line=hit.end_line,
        )
        assert hit.excerpt.strip() == physical.strip()


def test_search_code_enforces_per_file_budget(tmp_path: Path) -> None:
    """Verify a single file with many matches cannot consume the whole result budget."""
    source = [f"def handler_{i}():\n    raise BoomError('{i}')\n" for i in range(8)]
    (tmp_path / "handlers.py").write_text("\n".join(source), encoding="utf-8")
    snapshot = RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="sample/budget",
        commit_sha="budget123",
    )

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query="BoomError",
            repo_snapshot=snapshot,
            max_results=10,
            max_results_per_file=2,
        )

    assert len(hits) == 2
    assert all(hit.path == "handlers.py" for hit in hits)


def test_search_code_anchors_explicitly_named_file(tmp_path: Path) -> None:
    """Verify a file named in the issue is retrieved even without prose matches."""
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)
    (package / "database.py").write_text(
        "def connect():\n    return True\n", encoding="utf-8"
    )
    snapshot = RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="sample/anchor",
        commit_sha="anchor123",
    )

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query="Application crashes on startup in src/pkg/database.py",
            repo_snapshot=snapshot,
        )

    assert hits
    assert hits[0].path == "src/pkg/database.py"
    assert "def connect" in hits[0].excerpt

