"""Document chunking with heading and line-range provenance tracking."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class DocumentChunk:
    """A bounded passage of documentation or issue prose with exact provenance."""

    chunk_id: str
    source_path: str
    source_type: Literal["documentation", "issue"]
    heading: str | None
    start_line: int
    end_line: int
    content: str
    content_hash: str
    commit_sha: str | None = None
    token_count: int = 0


def _compute_sha256(text: str) -> str:
    """Compute deterministic SHA-256 hex digest of string content."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_HEADING_REGEX = re.compile(r"^(#{1,6})\s+(.+)$")


def chunk_markdown(
    content: str,
    source_path: str,
    commit_sha: str | None = None,
    min_chunk_chars: int = 60,
    max_chunk_chars: int = 1500,
) -> list[DocumentChunk]:
    """Split markdown text into semantic chunks by headings and paragraph boundaries.

    Tracks 1-indexed start and end line ranges corresponding to the physical lines
    in the document so citations can be verified against disk bytes.
    """
    if not content or not content.strip():
        return []

    lines = content.splitlines()
    if not lines:
        return []

    # First pass: identify sections divided by Markdown headings
    sections: list[dict[str, Any]] = []
    current_heading: str | None = None
    current_lines: list[tuple[int, str]] = []  # (1-indexed line_num, line_str)

    for line_idx, line in enumerate(lines, start=1):
        heading_match = _HEADING_REGEX.match(line)
        if heading_match:
            if current_lines:
                sections.append({
                    "heading": current_heading,
                    "lines": current_lines,
                })
                current_lines = []
            current_heading = heading_match.group(2).strip()
            current_lines.append((line_idx, line))
        else:
            current_lines.append((line_idx, line))

    if current_lines:
        sections.append({
            "heading": current_heading,
            "lines": current_lines,
        })

    raw_chunks: list[dict[str, Any]] = []

    for sec in sections:
        sec_heading: str | None = sec["heading"]
        sec_lines: list[tuple[int, str]] = sec["lines"]
        if not sec_lines:
            continue

        # Trim leading and trailing empty lines to accurately bound non-empty text
        while sec_lines and not sec_lines[0][1].strip():
            sec_lines.pop(0)
        while sec_lines and not sec_lines[-1][1].strip():
            sec_lines.pop()

        if not sec_lines:
            continue

        sec_text = "\n".join(text for _, text in sec_lines)
        if len(sec_text) <= max_chunk_chars:
            raw_chunks.append({
                "heading": sec_heading,
                "start_line": sec_lines[0][0],
                "end_line": sec_lines[-1][0],
                "content": sec_text.strip(),
            })
        else:
            # Sub-split large section by paragraph boundaries (empty lines)
            para_lines: list[tuple[int, str]] = []
            for line_no, text in sec_lines:
                if not text.strip() and para_lines:
                    while para_lines and not para_lines[0][1].strip():
                        para_lines.pop(0)
                    while para_lines and not para_lines[-1][1].strip():
                        para_lines.pop()
                    if para_lines:
                        p_text = "\n".join(t for _, t in para_lines).strip()
                        raw_chunks.append({
                            "heading": sec_heading,
                            "start_line": para_lines[0][0],
                            "end_line": para_lines[-1][0],
                            "content": p_text,
                        })
                    para_lines = []
                else:
                    para_lines.append((line_no, text))

            while para_lines and not para_lines[0][1].strip():
                para_lines.pop(0)
            while para_lines and not para_lines[-1][1].strip():
                para_lines.pop()
            if para_lines:
                p_text = "\n".join(t for _, t in para_lines).strip()
                if p_text:
                    raw_chunks.append({
                        "heading": sec_heading,
                        "start_line": para_lines[0][0],
                        "end_line": para_lines[-1][0],
                        "content": p_text,
                    })

    # Merge tiny chunks into adjacent chunks to avoid fragment starvation
    merged_chunks: list[dict[str, Any]] = []
    for chunk in raw_chunks:
        if not chunk["content"]:
            continue
        if merged_chunks and len(chunk["content"]) < min_chunk_chars:
            prev = merged_chunks[-1]
            combined_len = len(prev["content"]) + len(chunk["content"]) + 1
            if combined_len <= max_chunk_chars:
                prev["content"] = f"{prev['content']}\n\n{chunk['content']}"
                prev["end_line"] = chunk["end_line"]
                continue

        merged_chunks.append(chunk)

    # Build final immutable DocumentChunk instances
    result: list[DocumentChunk] = []
    for idx, c in enumerate(merged_chunks, start=1):
        content_str = c["content"]
        words = content_str.split()
        safe_hash = _compute_sha256(content_str)
        chunk_id = f"doc_{_compute_sha256(source_path)[:8]}_{idx}"

        result.append(
            DocumentChunk(
                chunk_id=chunk_id,
                source_path=source_path,
                source_type="documentation",
                heading=c["heading"],
                start_line=c["start_line"],
                end_line=c["end_line"],
                content=content_str,
                content_hash=safe_hash,
                commit_sha=commit_sha,
                token_count=len(words),
            )
        )

    return result


def chunk_issue_context(
    issue_title: str,
    issue_body: str,
    comments: list[str],
    issue_number: int,
    commit_sha: str | None = None,
) -> list[DocumentChunk]:
    """Chunk GitHub issue title, body, and comments for document search indexing."""
    chunks: list[DocumentChunk] = []
    source_path = f"issue:{issue_number}"

    # Issue body chunk
    body_text = f"Title: {issue_title}\n\n{issue_body}".strip()
    if body_text:
        body_lines = body_text.splitlines()
        chunks.append(
            DocumentChunk(
                chunk_id=f"issue_{issue_number}_body",
                source_path=source_path,
                source_type="issue",
                heading="Issue Description",
                start_line=1,
                end_line=len(body_lines),
                content=body_text,
                content_hash=_compute_sha256(body_text),
                commit_sha=commit_sha,
                token_count=len(body_text.split()),
            )
        )

    # Individual comment chunks
    current_line = len(body_text.splitlines()) + 2
    for c_idx, comment_body in enumerate(comments, start=1):
        clean_comment = comment_body.strip()
        if not clean_comment:
            continue
        c_lines = clean_comment.splitlines()
        end_line = current_line + len(c_lines) - 1
        chunks.append(
            DocumentChunk(
                chunk_id=f"issue_{issue_number}_c{c_idx}",
                source_path=source_path,
                source_type="issue",
                heading=f"Comment #{c_idx}",
                start_line=current_line,
                end_line=end_line,
                content=clean_comment,
                content_hash=_compute_sha256(clean_comment),
                commit_sha=commit_sha,
                token_count=len(clean_comment.split()),
            )
        )
        current_line = end_line + 2

    return chunks
