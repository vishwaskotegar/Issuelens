"""Document context provider and repository documentation retriever."""

from __future__ import annotations

from pathlib import Path

from issuelens.agent.report import EvidenceItem
from issuelens.core.logging import get_logger
from issuelens.github.models import GitHubIssueContext
from issuelens.repository.snapshot import DEFAULT_EXCLUDED_PATTERNS, RepoSnapshot
from issuelens.retrieval.chunking import (
    DocumentChunk,
    chunk_issue_context,
    chunk_markdown,
)
from issuelens.retrieval.hybrid import DenseEmbedderProtocol, HybridSearchEngine

logger = get_logger("issuelens.retrieval.documents")

DOCUMENTATION_EXTENSIONS: frozenset[str] = frozenset({
    ".md",
    ".rst",
    ".txt",
})

DOCUMENTATION_ROOT_NAMES: frozenset[str] = frozenset({
    "readme.md",
    "readme.rst",
    "readme.txt",
    "readme",
    "contributing.md",
    "contributing.rst",
    "architecture.md",
    "changelog.md",
    "history.md",
})


def _is_doc_candidate(relative_path: Path) -> bool:
    """Check if a relative path matches documentation file conventions."""
    name = relative_path.name.lower()

    # Exclude standard hidden, virtualenv, and cache directories
    for part in relative_path.parts[:-1]:
        if part.startswith(".") or part in ("node_modules", "venv", ".venv", "__pycache__"):
            return False

    # Check root documentation files
    if len(relative_path.parts) == 1 and name in DOCUMENTATION_ROOT_NAMES:
        return True

    # Check files inside docs/ or doc/ subdirectories
    return (
        len(relative_path.parts) > 1
        and relative_path.parts[0].lower() in ("docs", "doc")
        and relative_path.suffix.lower() in DOCUMENTATION_EXTENSIONS
    )


class DocumentRetriever:
    """Discovers, indexes, and retrieves passages from repository docs and issues."""

    def __init__(
        self,
        snapshot: RepoSnapshot | None = None,
        issue_context: GitHubIssueContext | None = None,
        dense_embedder: DenseEmbedderProtocol | None = None,
        max_doc_files: int = 50,
        max_file_bytes: int = 500_000,
    ) -> None:
        self.snapshot = snapshot
        self.issue_context = issue_context
        self.max_doc_files = max_doc_files
        self.max_file_bytes = max_file_bytes
        self.engine = HybridSearchEngine(dense_embedder=dense_embedder)
        self.chunks: list[DocumentChunk] = []
        self._build_index()

    def _discover_doc_files(self) -> list[tuple[str, Path]]:
        """Find documentation files within snapshot root obeying security exclusions."""
        if self.snapshot is None or not self.snapshot.root_path.exists():
            return []

        discovered: list[tuple[str, Path]] = []
        root = self.snapshot.root_path

        for file_path in root.rglob("*"):
            if not file_path.is_file():
                continue

            try:
                rel_path = file_path.relative_to(root)
            except ValueError:
                continue

            # Check snapshot exclusions
            if any(rel_path.match(p) for p in DEFAULT_EXCLUDED_PATTERNS):
                continue

            if _is_doc_candidate(rel_path):
                # Check size boundary
                try:
                    if file_path.stat().st_size <= self.max_file_bytes:
                        discovered.append((rel_path.as_posix(), file_path))
                except OSError:
                    continue

            if len(discovered) >= self.max_doc_files:
                break

        return discovered

    def _build_index(self) -> None:
        """Scan documentation files and issue context, chunk content, and populate search index."""
        all_chunks: list[DocumentChunk] = []

        # 1. Index repository documentation files
        doc_files = self._discover_doc_files()
        commit_sha = self.snapshot.commit_sha if self.snapshot else None

        for rel_posix, abs_path in doc_files:
            try:
                text = abs_path.read_text(encoding="utf-8", errors="replace")
                file_chunks = chunk_markdown(
                    content=text,
                    source_path=rel_posix,
                    commit_sha=commit_sha,
                )
                all_chunks.extend(file_chunks)
            except Exception as exc:
                logger.warning("Failed to read documentation file %s: %s", rel_posix, exc)

        # 2. Index issue context if provided
        if self.issue_context is not None:
            comments = [c.body for c in self.issue_context.comments]
            issue_chunks = chunk_issue_context(
                issue_title=self.issue_context.issue.title,
                issue_body=self.issue_context.issue.body,
                comments=comments,
                issue_number=self.issue_context.issue.number,
                commit_sha=commit_sha,
            )
            all_chunks.extend(issue_chunks)

        self.chunks = all_chunks
        self.engine.index(all_chunks)
        logger.info(
            "DocumentRetriever initialized with %d chunks across %d doc files and issue context",
            len(all_chunks),
            len(doc_files),
        )

    def retrieve_documents(
        self,
        query: str,
        max_results: int = 5,
        start_evidence_idx: int = 1,
    ) -> list[EvidenceItem]:
        """Search documentation index and return validated EvidenceItem instances."""
        if not self.chunks or not query.strip():
            return []

        hits = self.engine.search(query=query, top_k=max_results)
        evidence_items: list[EvidenceItem] = []

        for idx, (chunk, _) in enumerate(hits, start=start_evidence_idx):
            # Format excerpt with heading context if present
            excerpt = (
                f"[{chunk.heading}]\n{chunk.content}"
                if chunk.heading
                else chunk.content
            )

            evidence_items.append(
                EvidenceItem(
                    evidence_id=f"doc_ev_{idx}",
                    source_type=chunk.source_type,
                    path_or_url=chunk.source_path,
                    start_line=chunk.start_line,
                    end_line=chunk.end_line,
                    excerpt=excerpt,
                    commit_sha=chunk.commit_sha,
                )
            )

        return evidence_items
