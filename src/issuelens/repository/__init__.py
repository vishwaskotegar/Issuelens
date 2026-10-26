"""Repository inspection, snapshot management, and code search package."""

from issuelens.repository.code_search import CodeHit, search_code, search_symbols
from issuelens.repository.python_symbols import (
    SymbolLocation,
    find_enclosing_symbol,
    find_symbols_by_name,
    parse_python_symbols,
)
from issuelens.repository.snapshot import RepoSnapshot, create_snapshot_from_zip
from issuelens.repository.snippets import read_snippet

__all__ = [
    "CodeHit",
    "RepoSnapshot",
    "SymbolLocation",
    "create_snapshot_from_zip",
    "find_enclosing_symbol",
    "find_symbols_by_name",
    "parse_python_symbols",
    "read_snippet",
    "search_code",
    "search_symbols",
]
