"""Hybrid search engine combining BM25 lexical ranking and dense vectors with RRF."""

from __future__ import annotations

import math
from typing import Protocol, runtime_checkable

from issuelens.core.logging import get_logger
from issuelens.retrieval.chunking import DocumentChunk
from issuelens.retrieval.lexical import BM25Index

logger = get_logger("issuelens.retrieval.hybrid")


@runtime_checkable
class DenseEmbedderProtocol(Protocol):
    """Structural protocol for dense vector embedding models."""

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """Compute normalized vector embeddings for a batch of text chunks."""
        ...

    def embed_query(self, query: str) -> list[float]:
        """Compute normalized vector embedding for a single search query."""
        ...


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Compute cosine similarity between two float vectors."""
    if len(vec_a) != len(vec_b) or not vec_a:
        return 0.0

    dot_product = sum(a * b for a, b in zip(vec_a, vec_b, strict=False))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))

    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0

    return dot_product / (norm_a * norm_b)


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    k: int = 60,
) -> dict[str, float]:
    """Fuse multiple ranked lists of chunk IDs using Reciprocal Rank Fusion (RRF).

    Formula: RRF_score(d) = sum(1 / (k + rank_i(d))) for each ranking list i.
    Rank is 1-indexed. k is standard smoothing constant (default: 60).
    """
    fused_scores: dict[str, float] = {}

    for ranking in rankings:
        for rank_1_indexed, item_id in enumerate(ranking, start=1):
            rrf_val = 1.0 / (k + rank_1_indexed)
            fused_scores[item_id] = fused_scores.get(item_id, 0.0) + rrf_val

    return fused_scores


class HybridSearchEngine:
    """Dual-engine hybrid retriever combining Okapi BM25 and dense vector ranking."""

    def __init__(
        self,
        bm25_index: BM25Index | None = None,
        dense_embedder: DenseEmbedderProtocol | None = None,
        rrf_k: int = 60,
    ) -> None:
        self.bm25_index = bm25_index or BM25Index()
        self.dense_embedder = dense_embedder
        self.rrf_k = rrf_k
        self.chunks: list[DocumentChunk] = []
        self._chunk_by_id: dict[str, DocumentChunk] = {}
        self._embeddings: list[list[float]] = []

    def index(self, chunks: list[DocumentChunk]) -> None:
        """Index chunks across lexical BM25 and compute dense vectors if embedder is configured."""
        self.chunks = list(chunks)
        self._chunk_by_id = {c.chunk_id: c for c in chunks}
        self.bm25_index.index(chunks)
        self._embeddings = []

        if self.dense_embedder is not None and self.chunks:
            try:
                texts = [
                    f"{c.heading or ''}\n{c.content}"
                    for c in self.chunks
                ]
                self._embeddings = self.dense_embedder.embed_texts(texts)
                logger.info("Embedded %d document chunks for dense retrieval", len(texts))
            except Exception as exc:
                logger.warning(
                    "Dense embedding failed; falling back to pure BM25 lexical search: %s",
                    exc,
                )
                self._embeddings = []

    def search(self, query: str, top_k: int = 5) -> list[tuple[DocumentChunk, float]]:
        """Search chunks using hybrid RRF fusion or fallback to lexical BM25."""
        if not self.chunks:
            return []

        # 1. Always execute BM25 lexical search
        bm25_results = self.bm25_index.search(query, top_k=len(self.chunks))

        # If no dense embedder or embeddings failed, return pure BM25 results
        if not self.dense_embedder or not self._embeddings:
            return bm25_results[:top_k]

        # 2. Execute dense semantic search
        try:
            query_vec = self.dense_embedder.embed_query(query)
            dense_scores: list[tuple[int, float]] = []

            for idx, chunk_vec in enumerate(self._embeddings):
                sim = cosine_similarity(query_vec, chunk_vec)
                if sim > 0.0:
                    dense_scores.append((idx, sim))

            dense_scores.sort(key=lambda x: x[1], reverse=True)
        except Exception as exc:
            logger.warning(
                "Dense query embedding error; falling back to BM25 results: %s",
                exc,
            )
            return bm25_results[:top_k]

        # 3. Form rank orderings for RRF
        lexical_ranking = [chunk.chunk_id for chunk, _ in bm25_results]
        dense_ranking = [self.chunks[idx].chunk_id for idx, _ in dense_scores]

        # If neither engine produced results, return empty
        if not lexical_ranking and not dense_ranking:
            return []

        # 4. Fuse using Reciprocal Rank Fusion
        fused = reciprocal_rank_fusion(
            rankings=[lexical_ranking, dense_ranking],
            k=self.rrf_k,
        )

        # Sort descending by fused RRF score
        sorted_fused = sorted(fused.items(), key=lambda x: x[1], reverse=True)

        results: list[tuple[DocumentChunk, float]] = []
        for chunk_id, score in sorted_fused[:top_k]:
            if chunk_id in self._chunk_by_id:
                results.append((self._chunk_by_id[chunk_id], round(score, 6)))

        return results
