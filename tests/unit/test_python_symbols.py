"""Unit tests for AST-based Python symbol extraction and enclosing lookup."""

from pathlib import Path

from issuelens.repository.python_symbols import (
    find_enclosing_symbol,
    find_symbols_by_name,
    parse_python_symbols,
)

FIXTURE_ROOT = Path(__file__).resolve().parent.parent / "fixtures" / "sample_repo"


def test_parse_python_symbols_classes_methods_functions() -> None:
    """Verify parsing classes, methods, async methods, and top-level functions."""
    auth_file = FIXTURE_ROOT / "sample_pkg" / "auth.py"
    content = auth_file.read_text(encoding="utf-8")

    symbols = parse_python_symbols(content, "sample_pkg/auth.py")
    names = {s.name for s in symbols}
    qualified_names = {s.qualified_name for s in symbols}

    assert "TokenAuthenticator" in names
    assert "validate_token" in names
    assert "async_authenticate" in names
    assert "helper_hash_token" in names

    assert "TokenAuthenticator.validate_token" in qualified_names
    assert "TokenAuthenticator.async_authenticate" in qualified_names

    # Check symbol types
    sym_dict = {s.name: s for s in symbols}
    assert sym_dict["TokenAuthenticator"].symbol_type == "class"
    assert sym_dict["validate_token"].symbol_type == "method"
    assert sym_dict["async_authenticate"].symbol_type == "method"
    assert sym_dict["helper_hash_token"].symbol_type == "function"


def test_find_enclosing_symbol_innermost() -> None:
    """Verify enclosing symbol locator returns innermost matching symbol."""
    auth_file = FIXTURE_ROOT / "sample_pkg" / "auth.py"
    content = auth_file.read_text(encoding="utf-8")
    symbols = parse_python_symbols(content, "sample_pkg/auth.py")

    # In auth.py, validate_token is inside TokenAuthenticator
    validate_sym = next(s for s in symbols if s.name == "validate_token")
    mid_line = validate_sym.start_line + 1

    enclosing = find_enclosing_symbol(symbols, mid_line)
    assert enclosing is not None
    assert enclosing.name == "validate_token"
    assert enclosing.qualified_name == "TokenAuthenticator.validate_token"


def test_find_symbols_by_name() -> None:
    """Verify finding symbols by name or qualified name."""
    auth_file = FIXTURE_ROOT / "sample_pkg" / "auth.py"
    content = auth_file.read_text(encoding="utf-8")
    symbols = parse_python_symbols(content, "sample_pkg/auth.py")

    matches = find_symbols_by_name(symbols, "helper_hash_token")
    assert len(matches) == 1
    assert matches[0].symbol_type == "function"

    qual_matches = find_symbols_by_name(symbols, "TokenAuthenticator.validate_token")
    assert len(qual_matches) == 1
    assert qual_matches[0].name == "validate_token"


def test_syntax_error_handled_gracefully() -> None:
    """Verify files with syntax errors return empty symbols without crashing."""
    broken_file = FIXTURE_ROOT / "sample_pkg" / "broken_syntax.py"
    content = broken_file.read_text(encoding="utf-8")

    symbols = parse_python_symbols(content, "sample_pkg/broken_syntax.py")
    assert symbols == []


def test_empty_string_returns_empty() -> None:
    """Verify empty code returns empty symbols list."""
    assert parse_python_symbols("", "empty.py") == []
    assert parse_python_symbols("   \n", "empty.py") == []
