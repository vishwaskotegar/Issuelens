"""Document retrieval subsystem for IssueLens: chunking, BM25, and hybrid ranking."""

from issuelens.retrieval.chunking import (
    DocumentChunk,
    chunk_issue_context,
    chunk_markdown,
)
from issuelens.retrieval.documents import DocumentRetriever
from issuelens.retrieval.hybrid import (
    DenseEmbedderProtocol,
    HybridSearchEngine,
    reciprocal_rank_fusion,
)
from issuelens.retrieval.lexical import BM25Index, tokenize

__all__ = [
    "BM25Index",
    "DenseEmbedderProtocol",
    "DocumentChunk",
    "DocumentRetriever",
    "HybridSearchEngine",
    "chunk_issue_context",
    "chunk_markdown",
    "reciprocal_rank_fusion",
    "tokenize",
]
