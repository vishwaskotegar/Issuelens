"""AST-based Python symbol extraction without code execution."""

import ast
from dataclasses import dataclass
from typing import Literal

from issuelens.core.logging import get_logger

logger = get_logger("issuelens.repository.python_symbols")

SymbolType = Literal["function", "async_function", "class", "method"]


@dataclass(frozen=True)
class SymbolLocation:
    """Identified Python function, method, or class within a source file."""

    name: str
    symbol_type: SymbolType
    file_path: str
    start_line: int
    end_line: int
    parent_name: str | None = None
    docstring: str | None = None

    @property
    def qualified_name(self) -> str:
        """Full hierarchical identifier (e.g., 'TokenAuthenticator.validate_token')."""
        return f"{self.parent_name}.{self.name}" if self.parent_name else self.name


class _SymbolVisitor(ast.NodeVisitor):
    """AST visitor traversing functions, classes, and methods."""

    def __init__(self, file_path: str) -> None:
        self.file_path = file_path
        self.symbols: list[SymbolLocation] = []
        self._scope_stack: list[str] = []

    def _get_end_line(self, node: ast.AST) -> int:
        """Safely determine end line number across Python AST nodes."""
        end = getattr(node, "end_lineno", None)
        if end is not None:
            return int(end)
        # Fallback if end_lineno is missing: scan body children
        max_line = getattr(node, "lineno", 1)
        for child in ast.walk(node):
            child_line = getattr(child, "lineno", None)
            if child_line and child_line > max_line:
                max_line = child_line
        return max_line

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        start = node.lineno
        end = self._get_end_line(node)
        parent = self._scope_stack[-1] if self._scope_stack else None

        self.symbols.append(
            SymbolLocation(
                name=node.name,
                symbol_type="class",
                file_path=self.file_path,
                start_line=start,
                end_line=end,
                parent_name=parent,
                docstring=ast.get_docstring(node),
            )
        )

        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        start = node.lineno
        end = self._get_end_line(node)
        parent = self._scope_stack[-1] if self._scope_stack else None
        sym_type: SymbolType = "method" if parent else "function"

        self.symbols.append(
            SymbolLocation(
                name=node.name,
                symbol_type=sym_type,
                file_path=self.file_path,
                start_line=start,
                end_line=end,
                parent_name=parent,
                docstring=ast.get_docstring(node),
            )
        )

        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        start = node.lineno
        end = self._get_end_line(node)
        parent = self._scope_stack[-1] if self._scope_stack else None
        sym_type: SymbolType = "method" if parent else "async_function"

        self.symbols.append(
            SymbolLocation(
                name=node.name,
                symbol_type=sym_type,
                file_path=self.file_path,
                start_line=start,
                end_line=end,
                parent_name=parent,
                docstring=ast.get_docstring(node),
            )
        )

        self._scope_stack.append(node.name)
        self.generic_visit(node)
        self._scope_stack.pop()


def parse_python_symbols(source_code: str, file_path: str) -> list[SymbolLocation]:
    """Parse Python source code and extract functions, classes, and methods.

    Handles SyntaxError, ValueError, and encoding issues gracefully without failing.
    """
    if not source_code.strip():
        return []

    try:
        tree = ast.parse(source_code, filename=file_path)
    except (SyntaxError, ValueError, TypeError) as exc:
        logger.debug("Failed to parse AST for %s: %s", file_path, exc)
        return []

    visitor = _SymbolVisitor(file_path=file_path)
    visitor.visit(tree)
    return visitor.symbols


def find_enclosing_symbol(
    symbols: list[SymbolLocation], line_number: int
) -> SymbolLocation | None:
    """Find innermost enclosing symbol (function or class) spanning line_number."""
    candidates = [
        s for s in symbols if s.start_line <= line_number <= s.end_line
    ]
    if not candidates:
        return None

    # Return innermost candidate (smallest span)
    return min(candidates, key=lambda s: s.end_line - s.start_line)


def find_symbols_by_name(
    symbols: list[SymbolLocation], name: str, case_sensitive: bool = False
) -> list[SymbolLocation]:
    """Filter symbols by name or qualified name."""
    target = name if case_sensitive else name.lower()
    matches: list[SymbolLocation] = []

    for sym in symbols:
        sym_name = sym.name if case_sensitive else sym.name.lower()
        sym_qual = sym.qualified_name if case_sensitive else sym.qualified_name.lower()
        if sym_name == target or sym_qual == target:
            matches.append(sym)

    return matches
