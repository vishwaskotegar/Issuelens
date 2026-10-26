import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from issuelens.core.logging import get_logger
from issuelens.repository.python_symbols import (
    find_enclosing_symbol,
    find_symbols_by_name,
    parse_python_symbols,
)
from issuelens.repository.snapshot import RepoSnapshot
from issuelens.repository.snippets import read_snippet

logger = get_logger("issuelens.repository.code_search")


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


def extract_search_terms(query: str) -> list[str]:
    """Tokenize and extract significant search identifiers from user/issue query."""
    if not query.strip():
        return []

    # Preserve exact double-quoted phrases
    phrases = re.findall(r'"([^"]+)"', query)
    cleaned_query = re.sub(r'"[^"]+"', " ", query)

    # Extract word tokens: identifier characters, letters, digits, underscores, dots
    words = re.findall(r"[a-zA-Z_][a-zA-Z0-9_\.]*", cleaned_query)

    # Filter out common short noise words
    stop_words = {
        "and",
        "or",
        "the",
        "in",
        "on",
        "at",
        "to",
        "for",
        "of",
        "with",
        "a",
        "an",
        "is",
        "it",
        "this",
        "that",
        "issue",
        "bug",
        "error",
        "fail",
        "fails",
    }
    filtered_words = [w for w in words if len(w) > 2 and w.lower() not in stop_words]

    # Combine unique terms preserving order
    all_terms: list[str] = []
    for term in phrases + filtered_words:
        if term not in all_terms:
            all_terms.append(term)

    return all_terms or [query.strip()]


def _search_with_ripgrep(
    rg_binary: str,
    terms: list[str],
    snapshot: RepoSnapshot,
    max_raw_matches: int = 100,
) -> list[dict[str, Any]]:
    """Execute ripgrep command with JSON output and strict boundary constraints."""
    raw_hits: list[dict[str, Any]] = []

    for term in terms[:5]:  # Bound search to top terms
        cmd = [
            rg_binary,
            "--json",
            "--smart-case",
            "--max-count",
            str(max_raw_matches),
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
            return []

        for line in result.stdout.splitlines():
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            if data.get("type") == "match":
                payload = data.get("data", {})
                path_data = payload.get("path", {})
                raw_path = path_data.get("text", "")
                line_number = payload.get("line_number", 1)

                try:
                    rel_path = Path(raw_path).resolve().relative_to(snapshot.root_path).as_posix()
                except ValueError:
                    continue

                raw_hits.append(
                    {
                        "path": rel_path,
                        "line_number": line_number,
                        "term": term,
                    }
                )

    return raw_hits


def _search_with_python_fallback(
    terms: list[str],
    snapshot: RepoSnapshot,
    max_raw_matches: int = 100,
) -> list[dict[str, Any]]:
    """Deterministic, pure-Python lexical scanner fallback when ripgrep is absent."""
    raw_hits: list[dict[str, Any]] = []
    source_files = snapshot.list_source_files()

    compiled_patterns = [
        (term, re.compile(re.escape(term), re.IGNORECASE)) for term in terms
    ]

    for rel_path in source_files:
        try:
            full_path = snapshot.safe_resolve(rel_path)
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            continue

        for line_idx, line_text in enumerate(content.splitlines(), start=1):
            for term, pattern in compiled_patterns:
                if pattern.search(line_text):
                    raw_hits.append(
                        {
                            "path": rel_path,
                            "line_number": line_idx,
                            "term": term,
                        }
                    )
                    if len(raw_hits) >= max_raw_matches:
                        return raw_hits

    return raw_hits


def search_code(
    query: str,
    repo_snapshot: RepoSnapshot,
    max_results: int = 20,
    expand_symbols: bool = True,
    context_lines: int = 2,
) -> list[CodeHit]:
    """Search repository snapshot for code references matching query terms.

    Uses ripgrep when available on the host system; falls back deterministically
    to a pure-Python lexical scanner if ripgrep is not installed.
    """
    terms = extract_search_terms(query)
    if not terms:
        return []

    # 1. Discover raw hits via ripgrep or Python fallback
    rg_binary = find_ripgrep_executable()
    if rg_binary:
        logger.debug("Executing code search with ripgrep binary: %s", rg_binary)
        raw_hits = _search_with_ripgrep(rg_binary, terms, repo_snapshot)
    else:
        logger.debug("Ripgrep not detected; utilizing deterministic Python lexical search")
        raw_hits = _search_with_python_fallback(terms, repo_snapshot)

    # 2. Group matches by (path, approximate line range) to consolidate nearby hits
    grouped: dict[str, list[dict[str, Any]]] = {}
    for hit in raw_hits:
        grouped.setdefault(hit["path"], []).append(hit)

    code_hits: list[CodeHit] = []

    # Cache AST symbols per file to avoid redundant parsing
    ast_cache: dict[str, list[Any]] = {}

    for file_path, hits in grouped.items():
        is_python = file_path.endswith(".py")
        symbols: list[Any] = []

        if is_python and expand_symbols:
            if file_path not in ast_cache:
                try:
                    full_p = repo_snapshot.safe_resolve(file_path)
                    content = full_p.read_text(encoding="utf-8", errors="replace")
                    ast_cache[file_path] = parse_python_symbols(content, file_path)
                except Exception:
                    ast_cache[file_path] = []
            symbols = ast_cache[file_path]

        # Consolidate hits in this file
        seen_ranges: set[tuple[int, int]] = set()

        for h in hits:
            line_no = h["line_number"]
            start_l = line_no
            end_l = line_no
            symbol_name: str | None = None

            # Attempt AST symbol expansion for Python files
            if is_python and expand_symbols and symbols:
                enclosing = find_enclosing_symbol(symbols, line_no)
                if enclosing:
                    # Bound maximum expansion to 60 lines
                    if (enclosing.end_line - enclosing.start_line) <= 60:
                        start_l = enclosing.start_line
                        end_l = enclosing.end_line
                    symbol_name = enclosing.qualified_name

            range_key = (start_l, end_l)
            if range_key in seen_ranges:
                # Merge matched terms into existing hit
                for existing in code_hits:
                    if (
                        existing.path == file_path
                        and existing.start_line == start_l
                        and existing.end_line == end_l
                        and h["term"] not in existing.matched_terms
                    ):
                        existing.matched_terms.append(h["term"])
                continue

            seen_ranges.add(range_key)

            try:
                excerpt = read_snippet(
                    snapshot=repo_snapshot,
                    file_path=file_path,
                    start_line=start_l,
                    end_line=end_l,
                    context_lines=0 if symbol_name else context_lines,
                )
            except Exception as exc:
                logger.debug("Failed to read snippet for %s: %s", file_path, exc)
                continue

            code_hits.append(
                CodeHit(
                    path=file_path,
                    start_line=start_l,
                    end_line=end_l,
                    excerpt=excerpt,
                    matched_terms=[h["term"]],
                    commit_sha=repo_snapshot.commit_sha,
                    symbol_name=symbol_name,
                )
            )

            if len(code_hits) >= max_results:
                break

        if len(code_hits) >= max_results:
            break

    return code_hits[:max_results]


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
            excerpt = read_snippet(
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
