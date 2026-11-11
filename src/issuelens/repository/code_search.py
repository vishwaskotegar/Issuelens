"""Deterministic, relevance-ranked lexical code search over pinned snapshots."""

import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, Field

from issuelens.core.logging import get_logger
from issuelens.repository.python_symbols import (
    SymbolLocation,
    find_enclosing_symbol,
    find_symbols_by_name,
    parse_python_symbols,
)
from issuelens.repository.snapshot import RepoSnapshot
from issuelens.repository.snippets import read_snippet_with_bounds

logger = get_logger("issuelens.repository.code_search")

# Source-code extensions that should outrank prose/documentation matches.
SOURCE_CODE_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".cs",
    ".go",
    ".h",
    ".hpp",
    ".java",
    ".js",
    ".jsx",
    ".kt",
    ".lua",
    ".m",
    ".mm",
    ".php",
    ".pl",
    ".ps1",
    ".py",
    ".pyi",
    ".r",
    ".rb",
    ".rs",
    ".scala",
    ".sh",
    ".sql",
    ".swift",
    ".ts",
    ".tsx",
}

DOCUMENTATION_EXTENSIONS = {
    ".adoc",
    ".markdown",
    ".md",
    ".rdoc",
    ".rst",
    ".txt",
}

# File stems that are conventionally changelogs/licenses rather than useful evidence.
DOCUMENTATION_FILE_STEMS = {
    "authors",
    "changelog",
    "changes",
    "code_of_conduct",
    "contributing",
    "contributors",
    "governance",
    "history",
    "license",
    "news",
    "notice",
    "readme",
    "security",
    "support",
}

TEST_DIRECTORY_NAMES = {"__tests__", "spec", "specs", "test", "tests"}

# Common English noise words that are poor code identifiers. This is deliberately
# small: technical words such as "timeout" or "proxy" must remain searchable.
STOP_WORDS = {
    "a",
    "about",
    "after",
    "again",
    "all",
    "also",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "because",
    "been",
    "before",
    "being",
    "bug",
    "but",
    "by",
    "can",
    "cannot",
    "could",
    "did",
    "do",
    "does",
    "error",
    "expected",
    "fail",
    "fails",
    "few",
    "fix",
    "for",
    "from",
    "get",
    "gets",
    "had",
    "has",
    "have",
    "happens",
    "here",
    "how",
    "if",
    "in",
    "into",
    "is",
    "issue",
    "it",
    "its",
    "just",
    "like",
    "may",
    "might",
    "more",
    "most",
    "much",
    "must",
    "my",
    "no",
    "not",
    "now",
    "of",
    "on",
    "only",
    "or",
    "other",
    "our",
    "out",
    "over",
    "same",
    "should",
    "since",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "too",
    "under",
    "until",
    "up",
    "using",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "whenever",
    "where",
    "which",
    "while",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
}


class CodeHit(BaseModel):
    """A verified source code reference with exact line numbers and excerpt."""

    path: str = Field(..., description="Repository-relative file path")
    start_line: int = Field(..., description="1-indexed starting line number")
    end_line: int = Field(..., description="1-indexed ending line number")
    excerpt: str = Field(..., description="Safe line-numbered excerpt from pinned snapshot")
    matched_terms: list[str] = Field(
        default_factory=list, description="Terms or tokens that matched"
    )
    commit_sha: str = Field(..., description="Pinned commit SHA representing snapshot")
    symbol_name: str | None = Field(
        default=None, description="Enclosing class, method, or function name if resolved"
    )
    score: float = Field(
        default=0.0, description="Deterministic relevance score used for ranking"
    )


@dataclass(frozen=True)
class _RawHit:
    """Intermediate ripgrep/fallback match before ranking and deduplication."""

    path: str
    line_number: int
    term: str
    weight: int


def find_ripgrep_executable() -> str | None:
    """Locate ripgrep executable on PATH or known tool directories."""
    # 1. System PATH
    rg_path = shutil.which("rg") or shutil.which("rg.exe")
    if rg_path:
        return rg_path

    # 2. Known local IDE/editor install locations on Windows/Linux
    local_app_data = Path(os.environ.get("LOCALAPPDATA", ""))
    antigravity_rg = (
        local_app_data
        / "Programs"
        / "Antigravity IDE"
        / "resources"
        / "app"
        / "node_modules"
        / "@vscode"
        / "ripgrep"
        / "bin"
        / "rg.exe"
    )
    common_candidates = [
        antigravity_rg,
        Path(r"C:\Program Files\ripgrep\rg.exe"),
    ]
    for candidate in common_candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate)

    return None


def is_path_like_term(term: str) -> bool:
    """Return True if a term denotes a repository path or a file name."""
    normalized = term.replace("\\", "/")
    if "/" in normalized:
        return True
    suffix = PurePosixPath(normalized).suffix.lower()
    return suffix in SOURCE_CODE_EXTENSIONS or suffix in DOCUMENTATION_EXTENSIONS


def classify_path(path: str) -> Literal["code", "documentation"]:
    """Classify a repository-relative path as executable source code or documentation."""
    posix = PurePosixPath(path.replace("\\", "/"))
    suffix = posix.suffix.lower()
    if suffix in DOCUMENTATION_EXTENSIONS:
        return "documentation"
    if posix.stem.lower() in DOCUMENTATION_FILE_STEMS:
        return "documentation"
    if suffix in SOURCE_CODE_EXTENSIONS:
        return "code"
    # Unknown non-source file (config, data) is treated as documentation so it is
    # never mislabeled as executable code evidence.
    return "documentation"


def _is_test_path(path: str) -> bool:
    posix = PurePosixPath(path.replace("\\", "/"))
    parent_parts = {part.lower() for part in posix.parts[:-1]}
    if parent_parts & TEST_DIRECTORY_NAMES:
        return True
    name = posix.name.lower()
    return name.startswith("test_") or name.endswith(("_test.py", "_test.go", "_test.rs"))


def term_weight(term: str) -> int:
    """Score how strongly a term looks like a code identifier rather than prose."""
    if not term:
        return 0
    lower = term.lower()
    if lower in STOP_WORDS:
        return 0
    if is_path_like_term(term):
        return 8
    if re.search(r"(error|exception|warning)$", lower) and len(term) > 5:
        return 9
    if "_" in term:
        return 7
    if "." in term:
        return 6
    if re.search(r"[a-z][A-Z]", term) or re.fullmatch(r"[A-Z][A-Z0-9]{2,}", term):
        return 7
    if any(ch.isdigit() for ch in term):
        return 5
    if "-" in term:
        return 4
    if term.isalpha():
        return 3 if len(term) >= 6 else 2
    return 2


def is_high_signal_term(term: str) -> bool:
    """Return True for identifier-like/path-like terms suitable for precise retrieval."""
    return term_weight(term) >= 5


def extract_search_terms(query: str) -> list[str]:
    """Tokenize and extract significant search identifiers from user/issue query."""
    if not query.strip():
        return []

    # Preserve exact double-quoted phrases
    phrases = re.findall(r'"([^"]+)"', query)
    cleaned_query = re.sub(r'"[^"]+"', " ", query)

    # Extract word tokens including dotted and slash-separated path forms
    raw_tokens = re.findall(r"[a-zA-Z_][a-zA-Z0-9_./\-]*", cleaned_query)
    words = [token.strip(".-/") for token in raw_tokens]
    filtered_words = [
        word for word in words if len(word) > 2 and word.lower() not in STOP_WORDS
    ]

    # Combine unique terms preserving order
    all_terms: list[str] = []
    for term in phrases + filtered_words:
        if term and term not in all_terms:
            all_terms.append(term)

    # Expand qualified symbols such as "requests.exceptions.ProxyError" into their
    # final component ("ProxyError") so the actual class/function name is searchable.
    expanded: list[str] = []
    for term in all_terms:
        expanded.append(term)
        if "." in term and not is_path_like_term(term):
            tail = term.rsplit(".", 1)[-1]
            if tail and tail not in expanded and (tail[0].isupper() or "_" in tail):
                expanded.append(tail)

    return expanded or [query.strip()]


def select_search_terms(query: str, max_terms: int = 8) -> list[tuple[str, int]]:
    """Select the most discriminating query terms with deterministic weights.

    When at least three identifier-like terms exist, prose words are dropped
    entirely so a natural-language issue body cannot flood retrieval results.
    """
    terms = extract_search_terms(query)
    if not terms:
        return []

    weighted = [(term, term_weight(term)) for term in terms]
    strong = [(term, weight) for term, weight in weighted if weight >= 5]

    if len(strong) >= 3:
        return strong[:max_terms]

    ordered = strong + [
        (term, weight) for term, weight in weighted if 0 < weight < 5
    ]
    return ordered[:max_terms]


def _search_with_ripgrep(
    rg_binary: str,
    terms: list[tuple[str, int]],
    snapshot: RepoSnapshot,
    max_matches_per_file: int = 25,
) -> list[_RawHit]:
    """Execute ripgrep command with JSON output and strict boundary constraints."""
    raw_hits: list[_RawHit] = []

    for term, weight in terms:
        cmd = [
            rg_binary,
            "--json",
            "--fixed-strings",
            "--smart-case",
            "--max-count",
            str(max_matches_per_file),
            "--max-filesize",
            str(snapshot.max_file_bytes),
        ]

        # Apply directory and pattern exclusions
        for excluded_dir in snapshot.excluded_dirs:
            cmd.extend(["--glob", f"!{excluded_dir}/**"])
        for excluded_pat in snapshot.excluded_patterns:
            cmd.extend(["--glob", f"!{excluded_pat}"])

        cmd.extend(["-e", term, str(snapshot.root_path)])

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
                check=False,
            )
        except (subprocess.TimeoutExpired, OSError) as exc:
            logger.warning("Ripgrep execution failed or timed out: %s", exc)
            return raw_hits

        for line in result.stdout.splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "match":
                payload = data.get("data", {})
                raw_path = payload.get("path", {}).get("text", "")
                line_number = payload.get("line_number", 1)

                try:
                    rel_path = (
                        Path(raw_path).resolve().relative_to(snapshot.root_path).as_posix()
                    )
                except ValueError:
                    continue

                raw_hits.append(
                    _RawHit(
                        path=rel_path,
                        line_number=int(line_number),
                        term=term,
                        weight=weight,
                    )
                )

    return raw_hits


def _search_with_python_fallback(
    terms: list[tuple[str, int]],
    snapshot: RepoSnapshot,
    max_matches_per_file_term: int = 10,
    max_total_hits: int = 2000,
) -> list[_RawHit]:
    """Deterministic pure-Python lexical scanner fallback when ripgrep is absent."""
    raw_hits: list[_RawHit] = []
    source_files = snapshot.list_source_files()

    compiled_patterns = [
        (term, weight, re.compile(re.escape(term), re.IGNORECASE))
        for term, weight in terms
    ]

    for rel_path in source_files:
        try:
            full_path = snapshot.safe_resolve(rel_path)
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        per_term_counts: dict[str, int] = {}
        for line_idx, line_text in enumerate(content.splitlines(), start=1):
            for term, weight, pattern in compiled_patterns:
                if per_term_counts.get(term, 0) >= max_matches_per_file_term:
                    continue
                if pattern.search(line_text):
                    raw_hits.append(
                        _RawHit(
                            path=rel_path,
                            line_number=line_idx,
                            term=term,
                            weight=weight,
                        )
                    )
                    per_term_counts[term] = per_term_counts.get(term, 0) + 1
                    if len(raw_hits) >= max_total_hits:
                        return raw_hits

    return raw_hits


def _find_path_term_files(
    path_terms: list[str],
    snapshot: RepoSnapshot,
    max_matches: int = 20,
) -> dict[str, set[str]]:
    """Find repository files whose path or basename matches an explicit path term."""
    matches: dict[str, set[str]] = {}
    if not path_terms:
        return matches

    for rel_path in snapshot.list_source_files():
        posix = rel_path.replace("\\", "/")
        basename = PurePosixPath(posix).name
        for term in path_terms:
            cleaned = term.replace("\\", "/")
            if cleaned.startswith("./"):
                cleaned = cleaned[2:]
            if not cleaned:
                continue
            if posix == cleaned or basename == cleaned or posix.endswith(f"/{cleaned}"):
                matches.setdefault(rel_path, set()).add(term)
        if len(matches) >= max_matches:
            break

    return matches


def _file_priority(path: str) -> float:
    """Prefer production source files and penalize tests and prose documents."""
    priority = 4.0 if classify_path(path) == "code" else -3.0
    if _is_test_path(path):
        priority -= 2.0
    return priority


def _symbol_matches_terms(
    symbol_name: str,
    terms: list[tuple[str, int]],
) -> bool:
    if not symbol_name:
        return False
    lowered = symbol_name.lower()
    return any(
        weight >= 5 and term.lower() in lowered for term, weight in terms
    )


def _path_anchor_hits(
    path_matches: dict[str, set[str]],
    snapshot: RepoSnapshot,
) -> list[_RawHit]:
    """Create low-noise anchor hits for files explicitly named in the issue."""
    anchors: list[_RawHit] = []
    for rel_path, terms in sorted(path_matches.items()):
        term = sorted(terms)[0]
        line_number = 1
        if rel_path.endswith(".py"):
            try:
                content = snapshot.safe_resolve(rel_path).read_text(
                    encoding="utf-8", errors="replace"
                )
                symbols = parse_python_symbols(content, rel_path)
                if symbols:
                    line_number = symbols[0].start_line
            except Exception:
                logger.debug("Unable to parse path-anchored Python file %s", rel_path)
        anchors.append(
            _RawHit(
                path=rel_path,
                line_number=line_number,
                term=term,
                weight=term_weight(term),
            )
        )
    return anchors


def search_code(
    query: str,
    repo_snapshot: RepoSnapshot,
    max_results: int = 20,
    expand_symbols: bool = True,
    context_lines: int = 2,
    max_results_per_file: int = 3,
) -> list[CodeHit]:
    """Search a repository snapshot for code references matching query terms.

    Matches are scored deterministically and ranked globally so that a single
    file or changelog cannot consume the result budget. Code files outrank
    documentation/tests, explicit identifier matches outrank prose matches, and
    every returned excerpt covers exactly ``start_line..end_line``.

    Uses ripgrep when available on the host system; falls back to a pure-Python
    lexical scanner if ripgrep is not installed.
    """
    weighted_terms = select_search_terms(query)
    if not weighted_terms:
        return []

    path_terms = [term for term, _ in weighted_terms if is_path_like_term(term)]
    content_terms = [
        (term, weight) for term, weight in weighted_terms if not is_path_like_term(term)
    ]

    # 1. Discover raw content hits via ripgrep or Python fallback
    raw_hits: list[_RawHit] = []
    if content_terms:
        rg_binary = find_ripgrep_executable()
        if rg_binary:
            logger.debug("Executing code search with ripgrep binary: %s", rg_binary)
            raw_hits = _search_with_ripgrep(rg_binary, content_terms, repo_snapshot)
        else:
            logger.debug(
                "Ripgrep not detected; utilizing deterministic Python lexical search"
            )
            raw_hits = _search_with_python_fallback(content_terms, repo_snapshot)

    # 2. Detect files explicitly referenced by path in the issue text
    path_matches = _find_path_term_files(path_terms, repo_snapshot)
    raw_hits.extend(_path_anchor_hits(path_matches, repo_snapshot))

    if not raw_hits:
        return []

    # 3. Parse AST symbols lazily per Python file
    ast_cache: dict[str, list[SymbolLocation]] = {}

    def symbols_for(file_path: str) -> list[SymbolLocation]:
        if not file_path.endswith(".py") or not expand_symbols:
            return []
        if file_path not in ast_cache:
            try:
                content = repo_snapshot.safe_resolve(file_path).read_text(
                    encoding="utf-8", errors="replace"
                )
                ast_cache[file_path] = parse_python_symbols(content, file_path)
            except Exception:
                ast_cache[file_path] = []
        return ast_cache[file_path]

    # 4. Resolve windows, score and merge duplicates
    merged: dict[tuple[str, int, int], dict[str, Any]] = {}

    for raw_hit in raw_hits:
        symbols = symbols_for(raw_hit.path)
        symbol_name: str | None = None
        start_line = raw_hit.line_number
        end_line = raw_hit.line_number

        enclosing = (
            find_enclosing_symbol(symbols, raw_hit.line_number) if symbols else None
        )
        if enclosing is not None and (enclosing.end_line - enclosing.start_line) <= 60:
            start_line = enclosing.start_line
            end_line = enclosing.end_line
            symbol_name = enclosing.qualified_name

        try:
            actual_start, actual_end, excerpt = read_snippet_with_bounds(
                snapshot=repo_snapshot,
                file_path=raw_hit.path,
                start_line=start_line,
                end_line=end_line,
                context_lines=0 if symbol_name else context_lines,
            )
        except Exception as exc:
            logger.debug("Failed to read snippet for %s: %s", raw_hit.path, exc)
            continue

        if not excerpt:
            continue

        score = float(raw_hit.weight) + _file_priority(raw_hit.path)
        if raw_hit.path in path_matches:
            score += 6.0
        if symbol_name and _symbol_matches_terms(symbol_name, weighted_terms):
            score += 5.0

        key = (raw_hit.path, actual_start, actual_end)
        existing = merged.get(key)
        if existing is None:
            merged[key] = {
                "path": raw_hit.path,
                "start_line": actual_start,
                "end_line": actual_end,
                "excerpt": excerpt,
                "matched_terms": [raw_hit.term],
                "symbol_name": symbol_name,
                "score": score,
            }
        else:
            if raw_hit.term not in existing["matched_terms"]:
                existing["matched_terms"].append(raw_hit.term)
                existing["score"] = max(float(existing["score"]), score) + 0.25

    # 5. Rank globally, then enforce a per-file budget before the global budget
    ranked = sorted(
        merged.values(),
        key=lambda item: (-float(item["score"]), str(item["path"]), int(item["start_line"])),
    )

    code_hits: list[CodeHit] = []
    per_file_counts: dict[str, int] = {}
    for item in ranked:
        path = str(item["path"])
        if per_file_counts.get(path, 0) >= max_results_per_file:
            continue
        per_file_counts[path] = per_file_counts.get(path, 0) + 1
        code_hits.append(
            CodeHit(
                path=path,
                start_line=int(item["start_line"]),
                end_line=int(item["end_line"]),
                excerpt=str(item["excerpt"]),
                matched_terms=list(item["matched_terms"]),
                commit_sha=repo_snapshot.commit_sha,
                symbol_name=item["symbol_name"],
                score=float(item["score"]),
            )
        )
        if len(code_hits) >= max_results:
            break

    return code_hits


def search_symbols(
    symbol_name: str,
    repo_snapshot: RepoSnapshot,
    max_results: int = 20,
) -> list[CodeHit]:
    """Search specifically for Python function or class declarations by name."""
    source_files = repo_snapshot.list_source_files(extensions={".py"})
    hits: list[CodeHit] = []

    for file_path in source_files:
        try:
            full_path = repo_snapshot.safe_resolve(file_path)
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        symbols = parse_python_symbols(content, file_path)
        matched_symbols = find_symbols_by_name(symbols, symbol_name)

        for sym in matched_symbols:
            _, _, excerpt = read_snippet_with_bounds(
                snapshot=repo_snapshot,
                file_path=file_path,
                start_line=sym.start_line,
                end_line=sym.end_line,
                context_lines=0,
            )
            hits.append(
                CodeHit(
                    path=file_path,
                    start_line=sym.start_line,
                    end_line=sym.end_line,
                    excerpt=excerpt,
                    matched_terms=[symbol_name],
                    commit_sha=repo_snapshot.commit_sha,
                    symbol_name=sym.qualified_name,
                )
            )
            if len(hits) >= max_results:
                return hits

    return hits
