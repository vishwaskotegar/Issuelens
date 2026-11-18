"""Unit tests for Phase 8 Document RAG: chunking, Okapi BM25, and hybrid RRF retrieval."""

from pathlib import Path
from unittest.mock import MagicMock

from issuelens.repository.snapshot import RepoSnapshot
from issuelens.retrieval.chunking import (
    chunk_issue_context,
    chunk_markdown,
)
from issuelens.retrieval.documents import DocumentRetriever
from issuelens.retrieval.hybrid import (
    DenseEmbedderProtocol,
    HybridSearchEngine,
    cosine_similarity,
    reciprocal_rank_fusion,
)
from issuelens.retrieval.lexical import BM25Index, tokenize


def test_tokenize_filters_stop_words_and_punctuation() -> None:
    """Verify tokenizer extracts identifiers and drops English stop words."""
    tokens = tokenize("Fixing the database_connection timeout after 30 seconds!")
    assert "database_connection" in tokens
    assert "timeout" in tokens
    assert "30" in tokens
    assert "seconds" in tokens
    assert "the" not in tokens
    assert "after" not in tokens


def test_chunk_markdown_preserves_line_numbers_and_headings() -> None:
    """Verify Markdown chunker records exact 1-indexed line spans and headings."""
    sample_md = (
        "# Architecture Overview\n"
        "\n"
        "IssueLens is an evidence-backed debugging assistant.\n"
        "It inspects GitHub issues and repository code.\n"
        "\n"
        "## Configuration Options\n"
        "\n"
        "You can configure DATABASE_TIMEOUT to control connection pooling.\n"
        "The default timeout value is 30 seconds.\n"
        "\n"
        "### Security Boundary\n"
        "\n"
        "The system never executes arbitrary repository code or tests.\n"
    )

    chunks = chunk_markdown(sample_md, source_path="docs/architecture.md", commit_sha="abc12345")

    assert len(chunks) == 3
    # First chunk: Architecture Overview
    c1 = chunks[0]
    assert c1.heading == "Architecture Overview"
    assert c1.start_line == 1
    assert c1.end_line == 4
    assert c1.source_path == "docs/architecture.md"
    assert c1.source_type == "documentation"
    assert c1.commit_sha == "abc12345"
    assert len(c1.content_hash) == 64

    # Second chunk: Configuration Options
    c2 = chunks[1]
    assert c2.heading == "Configuration Options"
    assert "DATABASE_TIMEOUT" in c2.content
    assert c2.start_line == 6
    assert c2.end_line == 9

    # Third chunk: Security Boundary
    c3 = chunks[2]
    assert c3.heading == "Security Boundary"
    assert "never executes" in c3.content


def test_chunk_markdown_empty_or_whitespace() -> None:
    """Verify empty or whitespace strings return empty chunk lists safely."""
    assert chunk_markdown("", "README.md") == []
    assert chunk_markdown("   \n\n  ", "README.md") == []


def test_chunk_issue_context() -> None:
    """Verify issue description and comments are chunked with provenance."""
    chunks = chunk_issue_context(
        issue_title="Timeout when connecting to Postgres",
        issue_body="Connection times out after 10 seconds under high concurrency.",
        comments=[
            "Can reproduce on Python 3.12 with psycopg3.",
            "Setting pool_size=20 resolves the immediate deadlock.",
        ],
        issue_number=42,
        commit_sha="c0ffee",
    )

    assert len(chunks) == 3
    body_chunk = chunks[0]
    assert body_chunk.source_type == "issue"
    assert body_chunk.heading == "Issue Description"
    assert "Title: Timeout when connecting to Postgres" in body_chunk.content
    assert body_chunk.start_line == 1

    c1 = chunks[1]
    assert c1.heading == "Comment #1"
    assert "Python 3.12" in c1.content
    assert c1.start_line > body_chunk.end_line

    c2 = chunks[2]
    assert c2.heading == "Comment #2"
    assert "pool_size=20" in c2.content


def test_bm25_lexical_ranking() -> None:
    """Verify Okapi BM25 scores high-relevance chunks higher than low-relevance chunks."""
    doc_text = (
        "# Database Setup\n"
        "Configure database connection pool with pool_size and max_overflow settings.\n"
        "Ensure pool_timeout is calibrated for network latency.\n"
        "\n"
        "# Logging Policy\n"
        "All application errors must be logged with structured JSON formatting.\n"
        "Ensure secrets and API keys are redacted.\n"
        "\n"
        "# User Authentication\n"
        "JWT tokens expire after 1 hour. Refresh tokens must be rotated.\n"
    )

    chunks = chunk_markdown(doc_text, source_path="README.md")
    index = BM25Index()
    index.index(chunks)

    results = index.search(query="connection pool timeout latency", top_k=2)
    assert len(results) >= 1
    top_chunk, top_score = results[0]
    assert top_chunk.heading == "Database Setup"
    assert top_score > 0.0

    # Query about logging should prioritize Logging Policy
    log_results = index.search(query="structured JSON logging redaction", top_k=2)
    assert len(log_results) >= 1
    assert log_results[0][0].heading == "Logging Policy"


def test_reciprocal_rank_fusion_math() -> None:
    """Verify standard RRF math sum(1 / (k + rank))."""
    # 2 rankings with standard k=60
    # doc_A is rank 1 in list1 and rank 2 in list2
    # doc_B is rank 2 in list1 and rank 1 in list2
    # doc_C is rank 3 in list1 only
    rankings = [
        ["doc_A", "doc_B", "doc_C"],
        ["doc_B", "doc_A"],
    ]
    fused = reciprocal_rank_fusion(rankings, k=60)

    # doc_A: 1/(60+1) + 1/(60+2) = 1/61 + 1/62
    expected_a = (1.0 / 61.0) + (1.0 / 62.0)
    assert abs(fused["doc_A"] - expected_a) < 1e-6
    # doc_B has the same sum
    assert abs(fused["doc_B"] - expected_a) < 1e-6
    # doc_C: 1/(60+3) = 1/63
    assert abs(fused["doc_C"] - (1.0 / 63.0)) < 1e-6
    # doc_A and doc_B beat doc_C
    assert fused["doc_A"] > fused["doc_C"]


def test_cosine_similarity() -> None:
    """Verify cosine similarity calculation between identical and orthogonal vectors."""
    v1 = [1.0, 0.0, 0.0]
    v2 = [1.0, 0.0, 0.0]
    v3 = [0.0, 1.0, 0.0]

    assert abs(cosine_similarity(v1, v2) - 1.0) < 1e-6
    assert abs(cosine_similarity(v1, v3) - 0.0) < 1e-6
    assert cosine_similarity([], []) == 0.0


def test_hybrid_search_with_mock_embedder() -> None:
    """Verify HybridSearchEngine combines lexical and dense rankings."""
    doc_text = (
        "# Configuration\n"
        "Sets max database workers for background task processing.\n"
        "\n"
        "# Error Handling\n"
        "Retry transient network errors up to three times with exponential backoff.\n"
    )
    chunks = chunk_markdown(doc_text, source_path="README.md")

    # Mock embedder assigning synthetic 2D vectors
    mock_embedder = MagicMock(spec=DenseEmbedderProtocol)
    # chunk 0: [1.0, 0.0], chunk 1: [0.0, 1.0]
    mock_embedder.embed_texts.return_value = [[1.0, 0.0], [0.0, 1.0]]
    # Query vector close to chunk 1
    mock_embedder.embed_query.return_value = [0.1, 0.9]

    engine = HybridSearchEngine(dense_embedder=mock_embedder)
    engine.index(chunks)

    results = engine.search(query="network error backoff", top_k=2)
    assert len(results) == 2
    top_chunk, score = results[0]
    assert top_chunk.heading == "Error Handling"
    assert score > 0.0


def test_hybrid_search_fallback_on_embedder_failure() -> None:
    """Verify HybridSearchEngine gracefully falls back to BM25 if embedder fails."""
    doc_text = (
        "# Fast Cache\n"
        "In-memory Redis cache with TTL expiration.\n"
    )
    chunks = chunk_markdown(doc_text, source_path="README.md")

    failing_embedder = MagicMock(spec=DenseEmbedderProtocol)
    failing_embedder.embed_texts.side_effect = RuntimeError("Embedding service down")

    engine = HybridSearchEngine(dense_embedder=failing_embedder)
    engine.index(chunks)

    # Should fall back to BM25 without throwing
    results = engine.search(query="Redis cache", top_k=1)
    assert len(results) == 1
    assert results[0][0].heading == "Fast Cache"


def test_document_retriever_discovers_and_indexes_snapshot(tmp_path: Path) -> None:
    """Verify DocumentRetriever scans repository snapshot files and emits EvidenceItem."""
    # Create fake repository structure
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Welcome to ProjectX\n"
        "ProjectX is an async worker pipeline.\n"
        "\n"
        "## Concurrency Tuning\n"
        "Configure WORKER_CONCURRENCY=8 in your environment.\n",
        encoding="utf-8",
    )

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    guide = docs_dir / "troubleshooting.md"
    guide.write_text(
        "# Troubleshooting Guide\n"
        "\n"
        "### Database Connection Spikes\n"
        "If you see ConnectionTimeoutError, increase POOL_RECYCLE to 1800 seconds.\n",
        encoding="utf-8",
    )

    snapshot = RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="org/projectx",
        commit_sha="fedcba9876543210fedcba9876543210fedcba98",
    )

    retriever = DocumentRetriever(snapshot=snapshot)
    evidence = retriever.retrieve_documents(
        query="ConnectionTimeoutError POOL_RECYCLE",
        max_results=3,
    )

    assert len(evidence) >= 1
    top_item = evidence[0]
    assert top_item.source_type == "documentation"
    assert top_item.path_or_url == "docs/troubleshooting.md"
    assert "Database Connection Spikes" in top_item.excerpt
    assert "POOL_RECYCLE" in top_item.excerpt
    assert top_item.start_line is not None and top_item.start_line > 0
    assert top_item.end_line is not None and top_item.end_line >= top_item.start_line
    assert top_item.commit_sha == "fedcba9876543210fedcba9876543210fedcba98"


def test_measured_retrieval_comparison(tmp_path: Path) -> None:
    """Demonstrate document RAG surfaces documentation that code search lacks."""
    # Scenario: A bug report complains about POOL_RECYCLE configuration error.
    # The source code handlers.py only implements generic business logic.
    # The configuration specification is documented in docs/configuration.md.
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True)
    code_file = src_dir / "handlers.py"
    code_file.write_text(
        "def process_order(order_id: int) -> bool:\n"
        "    return True\n",
        encoding="utf-8",
    )

    docs_dir = tmp_path / "docs"
    docs_dir.mkdir(parents=True)
    doc_file = docs_dir / "configuration.md"
    doc_file.write_text(
        "# Connection Pooling Configuration\n"
        "\n"
        "When running behind AWS RDS Proxy, set POOL_RECYCLE=900.\n"
        "Failing to set POOL_RECYCLE causes intermittent BrokenPipeError on idle connections.\n",
        encoding="utf-8",
    )

    snapshot = RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="org/pool-test",
        commit_sha="1122334455667788990011223344556677889900",
    )

    retriever = DocumentRetriever(snapshot=snapshot)
    evidence = retriever.retrieve_documents("BrokenPipeError POOL_RECYCLE idle connections")

    assert len(evidence) >= 1
    assert evidence[0].path_or_url == "docs/configuration.md"
    assert "POOL_RECYCLE=900" in evidence[0].excerpt
    assert evidence[0].source_type == "documentation"
