# ADR-009: Document RAG with Heading-Preserving Chunking and Reciprocal Rank Fusion

- **Status**: Accepted
- **Date**: 2026-11-18
- **Deciders**: Senior Architecture Team
- **Phase**: Phase 8: Document RAG
- **Nature of Rationale**: Original

---

## 1. Context and Problem

In bug report investigation, not all relevant context resides in executable source code:
1. Configuration parameters, environment variables, timeout recommendations, and architectural policies are frequently documented exclusively in `README.md`, `docs/`, or release notes rather than inline code docstrings.
2. Code search engines (ripgrep, AST lookups) focus on exact identifiers and function declarations. When a bug report mentions a concept or configuration policy (e.g. `POOL_RECYCLE` recommendations behind RDS proxies or concurrency limits) without matching a Python symbol, code search alone returns empty or low-signal results.
3. However, document retrieval poses distinct risks: arbitrary character-length chunking loses semantic boundaries and heading context, corrupts line coordinates (violating physical citation verification), and blindly embedding all markdown documents into heavy external vector stores introduces paid API dependencies, latency, and test unreliability.

We need a deterministic, hermetic document retrieval subsystem that preserves line-level citation validity, provides fast BM25 lexical ranking, and supports optional dense semantic search through Reciprocal Rank Fusion (RRF) without mandatory external vector database infrastructure.

---

## 2. Decision and Rationale

We implement `issuelens.retrieval` with the following architectural components:

1. **Heading-Preserving Markdown Chunker (`chunking.py`)**:
   - Chunks markdown files along heading boundaries (`#`, `##`, `###`) and paragraph double newlines (`\n\n`).
   - Maintains exact 1-indexed `start_line` and `end_line` ranges corresponding to the physical lines in the document so citations can be verified against disk bytes via `validate_citation()`.
   - Computes deterministic SHA-256 content hashes and enforces `min_chunk_chars` merging to prevent tiny fragment starvation.
   - Also chunks GitHub issue descriptions and comments under `source_type="issue"`.

2. **Deterministic Okapi BM25 Lexical Ranking (`lexical.py`)**:
   - Implemented in pure typed Python without external C-extensions or heavy packages.
   - Uses Robertson-Spärck Jones IDF with Lucene non-negative smoothing (`log(1 + (N - df + 0.5) / (df + 0.5))`), standard saturation parameters ($k_1 = 1.5, b = 0.75$), and lowercase tokenization filtering stop words.

3. **Hybrid Search Engine with Reciprocal Rank Fusion (`hybrid.py`)**:
   - Implements Reciprocal Rank Fusion ($RRF(d) = \sum \frac{1}{k + \text{rank}(d)}$, with standard $k=60$) to combine lexical and dense semantic rankings without requiring delicate score calibration.
   - Defines `DenseEmbedderProtocol(Protocol)`. When a dense embedder is absent or fails, the engine gracefully falls back to pure BM25 ranking, ensuring zero runtime fragility.

4. **Repository Snapshot Document Retriever (`documents.py`)**:
   - Safely scans snapshot root for documentation (`README*`, `docs/`, `doc/`, `CONTRIBUTING*`, `CHANGELOG*`, `HISTORY*`).
   - Enforces file size limits (`max_file_bytes`) and path exclusions (`DEFAULT_EXCLUDED_PATTERNS`).
   - Converts top matching chunks into strongly typed `EvidenceItem` models with `source_type="documentation"`.

5. **Agent Workflow Integration (`nodes.py`, `service.py`)**:
   - Integrates document retrieval into `InvestigationNodes.retrieve_initial` and `retrieve_followup` alongside code search, allowing the LLM to ground hypotheses in both source code and system documentation.

---

## 3. Alternatives Considered

### Alternative A: Dedicated Vector Database with Mandatory Embeddings (e.g. Qdrant / Milvus / Chroma)
- **Description**: Spin up an external vector database container and require every documentation file to be embedded via OpenAI/Cohere embeddings on startup.
- **Pros**: Dense semantic similarity out of the box.
- **Cons**: Requires running external daemon containers, mandates paid API keys in automated tests (violating hermetic testing principles), and significantly increases operational complexity.

### Alternative B: Blind Fixed-Size Character Chunking (e.g. 500 characters with 50-character overlap)
- **Description**: Slice files strictly by character offsets without parsing Markdown structure.
- **Pros**: Extremely simple to implement.
- **Cons**: Cuts sentences and code blocks in half, destroys heading context, and loses physical line-number provenance, causing citation validation failures.

---

## 4. Why the Alternatives Were Not Selected

1. **Alternative A fell short of IssueLens Minimal Dependency and Hermetic Testing rules**: Forcing an external vector store and mandatory paid embedding APIs makes unit tests dependent on network access or third-party billing accounts. Using standard Okapi BM25 with an optional `DenseEmbedderProtocol` delivers fast, deterministic search with zero mandatory external services.
2. **Alternative B violated Citation Integrity**: Fixed-size character chunking destroys line boundaries. IssueLens requires exact line numbers pinned to commit SHAs for citation validation.

---

## 5. Trade-offs and Consequences

### Benefits
- **Zero New External Dependencies**: Built entirely with Python standard libraries (`math`, `re`, `hashlib`, `dataclasses`).
- **Complementary Retrieval**: Surfaces configuration guidelines, architectural rules, and troubleshooting notes that code symbol search cannot discover.
- **Line-Level Provenance**: Chunks track exact 1-indexed lines and SHA-256 hashes, seamlessly passing `validate_citation()`.
- **Safe Fallback**: RRF combines rankings safely; embedder failures gracefully degrade to BM25.

### Drawbacks & Limitations
- Pure BM25 without dense embeddings relies on vocabulary overlap and synonyms present in the text.
- Large repositories with thousands of markdown documentation files are bounded by `max_doc_files` (default: 50) to prevent unbounded memory consumption.
