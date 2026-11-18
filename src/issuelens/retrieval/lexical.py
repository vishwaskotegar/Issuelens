"""Deterministic Okapi BM25 lexical ranking engine for documentation chunks."""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field

from issuelens.retrieval.chunking import DocumentChunk

# Common English and programming stop words to reduce noise in lexical matching
DEFAULT_STOP_WORDS: frozenset[str] = frozenset({
    "a", "about", "above", "after", "again", "against", "all", "am", "an", "and", "any",
    "are", "aren't", "as", "at", "be", "because", "been", "before", "being", "below",
    "between", "both", "but", "by", "can", "cannot", "could", "did", "do", "does",
    "doing", "down", "during", "each", "few", "for", "from", "further", "had", "has",
    "have", "having", "he", "her", "here", "hers", "herself", "him", "himself", "his",
    "how", "i", "if", "in", "into", "is", "isn't", "it", "it's", "its", "itself",
    "let's", "me", "more", "most", "my", "myself", "no", "nor", "not", "of", "off",
    "on", "once", "only", "or", "other", "ought", "our", "ours", "ourselves", "out",
    "over", "own", "same", "she", "should", "so", "some", "such", "than", "that",
    "the", "their", "theirs", "them", "themselves", "then", "there", "these", "they",
    "this", "those", "through", "to", "too", "under", "until", "up", "very", "was",
    "wasn't", "we", "were", "what", "when", "where", "which", "while", "who", "whom",
    "why", "with", "won't", "would", "you", "your", "yours", "yourself", "yourselves",
})

_WORD_PATTERN = re.compile(r"\b[a-zA-Z0-9_\-\.]{2,}\b")


def tokenize(text: str, stop_words: frozenset[str] = DEFAULT_STOP_WORDS) -> list[str]:
    """Tokenize text into lowercase alphanumeric identifiers, filtering stop words."""
    if not text:
        return []
    tokens = _WORD_PATTERN.findall(text.lower())
    return [t for t in tokens if t not in stop_words]


@dataclass
class BM25Index:
    """In-memory Okapi BM25 ranking index over a collection of DocumentChunks."""

    k1: float = 1.5
    b: float = 0.75
    chunks: list[DocumentChunk] = field(default_factory=list)
    doc_lengths: list[int] = field(default_factory=list)
    avgdl: float = 0.0
    doc_freqs: dict[str, int] = field(default_factory=dict)
    inverted_index: dict[str, list[tuple[int, int]]] = field(default_factory=dict)
    # doc_id -> list of tokens
    corpus_size: int = 0

    def index(self, chunks: list[DocumentChunk]) -> None:
        """Build the inverted index and document statistics from a chunk collection."""
        self.chunks = list(chunks)
        self.corpus_size = len(chunks)
        self.doc_lengths = []
        self.doc_freqs = {}
        self.inverted_index = {}

        if not chunks:
            self.avgdl = 0.0
            return

        total_length = 0
        for doc_idx, chunk in enumerate(chunks):
            # Include heading in indexed text with extra weight
            doc_text = f"{chunk.heading or ''} {chunk.heading or ''} {chunk.content}"
            tokens = tokenize(doc_text)
            doc_len = len(tokens)
            self.doc_lengths.append(doc_len)
            total_length += doc_len

            term_counts = Counter(tokens)
            for term, count in term_counts.items():
                self.doc_freqs[term] = self.doc_freqs.get(term, 0) + 1
                if term not in self.inverted_index:
                    self.inverted_index[term] = []
                self.inverted_index[term].append((doc_idx, count))

        self.avgdl = total_length / self.corpus_size if self.corpus_size > 0 else 0.0

    def idf(self, term: str) -> float:
        """Compute Robertson-Spärck Jones IDF with Lucene non-negative smoothing."""
        df = self.doc_freqs.get(term, 0)
        if df == 0 or self.corpus_size == 0:
            return 0.0
        # log(1 + (N - df + 0.5) / (df + 0.5)) ensures IDF is always positive
        return math.log(1.0 + (self.corpus_size - df + 0.5) / (df + 0.5))

    def search(self, query: str, top_k: int = 5) -> list[tuple[DocumentChunk, float]]:
        """Score all documents against query terms and return top_k ranked matches."""
        if not self.chunks or self.corpus_size == 0:
            return []

        query_tokens = tokenize(query)
        if not query_tokens:
            return []

        scores: dict[int, float] = {}

        for term in query_tokens:
            if term not in self.inverted_index:
                continue

            term_idf = self.idf(term)
            if term_idf <= 0.0:
                continue

            postings = self.inverted_index[term]
            for doc_idx, tf in postings:
                doc_len = self.doc_lengths[doc_idx]
                denominator = tf + self.k1 * (1.0 - self.b + self.b * (doc_len / self.avgdl))
                if denominator == 0.0:
                    continue
                term_score = term_idf * (tf * (self.k1 + 1.0)) / denominator
                scores[doc_idx] = scores.get(doc_idx, 0.0) + term_score

        if not scores:
            return []

        # Sort descending by score
        ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)
        return [(self.chunks[doc_idx], round(score, 4)) for doc_idx, score in ranked[:top_k]]
