"""Safe line-numbered snippet reader for repository source files."""

from issuelens.core.errors import InvalidInputError
from issuelens.repository.snapshot import RepoSnapshot


def read_snippet(
    snapshot: RepoSnapshot,
    file_path: str,
    start_line: int,
    end_line: int,
    context_lines: int = 0,
    max_snippet_lines: int = 100,
) -> str:
    """Read line-numbered source code excerpt safely from snapshot.

    Args:
        snapshot: Active repository snapshot.
        file_path: Repository-relative file path.
        start_line: 1-indexed starting line number.
        end_line: 1-indexed ending line number (inclusive).
        context_lines: Count of surrounding lines to include before and after.
        max_snippet_lines: Safety ceiling on total lines returned.

    Returns:
        Formatted multi-line string prefixed with 1-indexed line numbers:
        '10: def example():\\n11:     return True'
    """
    if start_line < 1:
        raise InvalidInputError(f"Start line must be >= 1, got {start_line}.")
    if end_line < start_line:
        raise InvalidInputError(
            f"End line ({end_line}) cannot be less than start line ({start_line})."
        )

    resolved_path = snapshot.safe_resolve(file_path)

    try:
        content = resolved_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise InvalidInputError(f"Failed to read file '{file_path}': {exc}") from exc

    lines = content.splitlines()
    total_lines = len(lines)

    if total_lines == 0:
        return ""

    # Adjust window with context lines (clamped to 1 and total_lines)
    actual_start = max(1, start_line - context_lines)
    actual_end = min(total_lines, end_line + context_lines)

    if (actual_end - actual_start + 1) > max_snippet_lines:
        actual_end = actual_start + max_snippet_lines - 1

    snippet_lines: list[str] = []
    for line_num in range(actual_start, actual_end + 1):
        line_idx = line_num - 1
        line_text = lines[line_idx] if line_idx < total_lines else ""
        snippet_lines.append(f"{line_num}: {line_text}")

    return "\n".join(snippet_lines)
