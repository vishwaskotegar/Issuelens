"""Unit tests for deterministic code search and symbol lookup."""

from pathlib import Path
from unittest.mock import patch

from issuelens.repository.code_search import (
    CodeHit,
    classify_path,
    extract_search_terms,
    has_code_substance,
    is_high_signal_term,
    search_code,
    search_symbols,
    select_search_terms,
    term_weight,
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
    # Precondition: the fixture secret file must exist, or this test passes vacuously.
    assert "supersecretfixture123" in (FIXTURE_ROOT / ".env").read_text(encoding="utf-8")
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
    assert classify_path("docs_src/stream_data/tutorial002.py") == "documentation"
    assert classify_path("examples/quickstart.py") == "documentation"
    assert classify_path("tutorials/step1.py") == "documentation"
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


def test_has_code_substance() -> None:
    """Verify has_code_substance differentiates code from comments and blanks."""
    assert not has_code_substance("")
    assert not has_code_substance("   \n   \t\n")
    assert not has_code_substance("# Only a comment")
    assert not has_code_substance("193: # A line-numbered comment")
    assert not has_code_substance("193: # First comment\n194: # Second comment\n195: ")
    assert not has_code_substance("193: // JS/C style comment")

    assert has_code_substance("x = 1")
    assert has_code_substance("193: def calculate():")
    assert has_code_substance("193: # Comment\n194: x = 1\n195: # End")
    assert has_code_substance("10: value = True  # with trailing comment")


def test_search_code_rejects_comment_only_excerpts(tmp_path: Path) -> None:
    """Verify that comment-only excerpts in code files are rejected from code search results."""
    package = tmp_path / "src" / "pkg"
    package.mkdir(parents=True)

    # File with target identifier only inside comments
    (package / "comment_only.py").write_text(
        "# Header\n"
        "# Note: target_substance_token is documented here\n"
        "# End of commentary\n",
        encoding="utf-8",
    )

    # File with target identifier in executable code
    (package / "real_code.py").write_text(
        "def perform_work():\n"
        "    target_substance_token = 42\n"
        "    return target_substance_token\n",
        encoding="utf-8",
    )

    snapshot = RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="sample/substance",
        commit_sha="substance123",
    )

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query="target_substance_token",
            repo_snapshot=snapshot,
            max_results=5,
        )

    assert len(hits) == 1
    assert hits[0].path == "src/pkg/real_code.py"
    assert "target_substance_token = 42" in hits[0].excerpt
    assert not any(hit.path == "src/pkg/comment_only.py" for hit in hits)


def test_extract_search_terms_preserves_cli_flags() -> None:
    """Verify CLI option flags retain both flag format (--flag, -f) and stripped identifier."""
    query = "pytest --maxfail=1 -v --strict-markers -x"
    terms = extract_search_terms(query)

    assert "--maxfail" in terms
    assert "maxfail" in terms
    assert "--strict-markers" in terms
    assert "strict-markers" in terms
    assert "-v" in terms
    assert "v" in terms
    assert "-x" in terms
    assert "x" in terms
    assert "pytest" in terms


def test_term_weight_cli_flags() -> None:
    """Verify CLI flags starting with '--' receive top-tier weight (8)."""
    assert term_weight("--maxfail") == 8
    assert term_weight("--strict-markers") == 8
    assert term_weight("--tb") == 8
    assert term_weight("--capture") == 8
    assert is_high_signal_term("--maxfail") is True


def test_search_code_boosts_multi_term_co_occurrence(tmp_path: Path) -> None:
    """Verify that hits matching multiple distinct query terms receive a co-occurrence boost."""
    pkg = tmp_path / "src" / "pkg"
    pkg.mkdir(parents=True)

    # single_term.py matches only token_alpha
    (pkg / "single_term.py").write_text(
        "def process_alpha():\n"
        "    token_alpha = 100\n"
        "    return token_alpha\n",
        encoding="utf-8",
    )

    # multi_term.py matches both token_alpha and token_beta
    (pkg / "multi_term.py").write_text(
        "def process_both():\n"
        "    token_alpha = 1\n"
        "    token_beta = 2\n"
        "    return token_alpha + token_beta\n",
        encoding="utf-8",
    )

    snapshot = RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="sample/cooccurrence",
        commit_sha="cooccur123",
    )

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query="token_alpha token_beta",
            repo_snapshot=snapshot,
            max_results=5,
        )

    assert len(hits) >= 2
    # multi_term.py should outrank single_term.py due to multi-term co-occurrence boost (+3.0)
    assert hits[0].path == "src/pkg/multi_term.py"
    assert "token_alpha" in hits[0].matched_terms
    assert "token_beta" in hits[0].matched_terms
    assert hits[0].score > hits[1].score
    assert hits[0].score - hits[1].score >= 3.0


def test_search_code_surfaces_tests_matching_issue_or_pr_number(tmp_path: Path) -> None:
    """Verify that regression tests referencing issue or PR numbers surface with high relevance."""
    src_dir = tmp_path / "src" / "pkg"
    src_dir.mkdir(parents=True)
    tests_dir = tmp_path / "tests"
    tests_dir.mkdir(parents=True)

    # Generic production code
    (src_dir / "adapters.py").write_text(
        "class HTTPAdapter:\n"
        "    def send(self, request):\n"
        "        return 'response'\n",
        encoding="utf-8",
    )

    # Reproduction test explicitly addressing issue #6643
    (tests_dir / "test_adapters.py").write_text(
        "def test_regression_issue_6643():\n"
        "    # Test case verifying fix for #6643 regression\n"
        "    adapter = HTTPAdapter()\n"
        "    assert adapter.send(None) == 'response'\n",
        encoding="utf-8",
    )

    snapshot = RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="sample/repo",
        commit_sha="repro123",
    )

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query="Regression in #6643: HTTPAdapter issue",
            repo_snapshot=snapshot,
            max_results=5,
        )

    assert len(hits) >= 1
    test_hits = [h for h in hits if "test_adapters.py" in h.path]
    assert len(test_hits) >= 1
    assert "test_regression_issue_6643" in test_hits[0].excerpt
    assert test_hits[0].score > 0


def test_search_code_merges_overlapping_snippet_windows(tmp_path: Path) -> None:
    """Verify that adjacent/overlapping matches within a single file merge into a single hit."""
    src_dir = tmp_path / "src" / "pkg"
    src_dir.mkdir(parents=True)

    (src_dir / "service.py").write_text(
        "import os\n"
        "from typing import Optional\n"
        "\n"
        "class WorkerService:\n"
        "    def run_job(self, task_id: str) -> None:\n"
        "        worker_task_payload = self.load(task_id)\n"
        "        worker_task_result = self.execute(worker_task_payload)\n"
        "        return worker_task_result\n",
        encoding="utf-8",
    )

    snapshot = RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="sample/repo",
        commit_sha="merge123",
    )

    with patch("issuelens.repository.code_search.find_ripgrep_executable", return_value=None):
        hits = search_code(
            query="worker_task_payload worker_task_result",
            repo_snapshot=snapshot,
            max_results=10,
        )

    # Should consolidate the adjacent matches into one hit rather than 2 overlapping fragments
    file_hits = [h for h in hits if h.path == "src/pkg/service.py"]
    assert len(file_hits) == 1
    hit = file_hits[0]
    assert "worker_task_payload" in hit.matched_terms
    assert "worker_task_result" in hit.matched_terms
    assert "worker_task_payload = self.load" in hit.excerpt
    assert "worker_task_result = self.execute" in hit.excerpt






