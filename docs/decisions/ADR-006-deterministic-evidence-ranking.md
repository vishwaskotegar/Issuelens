# ADR-006: Deterministic Relevance Ranking for Lexical Code Evidence

- **Status**: Accepted
- **Date**: 2026-11-11
- **Deciders**: IssueLens engineering
- **Phase**: Phase 5: LangGraph Agent Workflow (retrieval quality follow-up)
- **Nature of Rationale**: Original

---

## 1. Context and Problem

The specification requires lexical retrieval to "normalize the issue into search
terms, run bounded searches, and return **ranked** matches with path and line
ranges" (`docs/BUILD_SPECIFICATION.md` §5.2, US-03). The Phase 2 baseline
implementation collected raw ripgrep matches, grouped them by file in filesystem
walk order, and truncated at `max_results`.

That baseline produced unusable evidence on a real repository. Investigating a
`ChunkedEncodingError` report against `psf/requests` returned ten items, all from
`HISTORY.md`, while the defining class in `src/requests/exceptions.py` and the
raising call site in `src/requests/models.py` never appeared. Three properties of
the input caused this:

1. Issue bodies are mostly prose, so unweighted tokenization produced generic
   terms such as `when`, `using`, and `large`.
2. Filesystem walk order is not relevance order; one matching changelog can
   exhaust the result budget before source files are visited.
3. Changelogs, docs, tests, and production code were indistinguishable.

The product contract also requires every excerpt to correspond to a declared
repository-relative line range at a pinned commit. Context padding made the
declared range disagree with the text shown to the model.

---

## 2. Decision and Rationale

Evidence retrieval uses an explicit, deterministic ranking pipeline in
`src/issuelens/repository/code_search.py`:

1. **Term weighting.** Every candidate term is classified as path-like,
   identifier-like (exception names, `snake_case`, `CamelCase`, dotted,
   digit-bearing), or prose. When at least three identifier-like terms exist,
   prose terms are dropped entirely; otherwise they fill the remaining budget.
   The issue parser additionally orders identifiers before prose in the query.
2. **Literal matching.** ripgrep runs with `--fixed-strings`, so identifiers
   containing `.` or other metacharacters are not interpreted as regexes.
3. **Path anchoring.** Files explicitly named in the issue are anchored as
   evidence (first AST symbol when available) and receive a ranking bonus.
4. **Scoring.** Each candidate hit scores: term weight + file priority
   (`code +4`, `documentation -3`, test path `-2`) + path-reference bonus `+6`
   + symbol-name match bonus `+5`.
5. **Deduplication and budgets.** Hits are deduplicated after AST expansion,
   sorted globally by `(-score, path, start_line)`, and capped per file
   (default 3) before the global `max_results` cap.
6. **Range integrity.** `read_snippet_with_bounds()` returns the actual
   clamped/expanded window, and `CodeHit.start_line`/`end_line` are set from
   that window so the excerpt always matches the citation.
7. **Source typing.** `classify_path()` labels each evidence item `code` or
   `documentation`; the validator verifies both against snapshot bytes.

Ranking is pure functions over retrieved hits. No LLM judgment, embedding
service, or persistent index participates in deciding which code is evidence.

---

## 3. Alternatives Considered

### Alternative A: Keep filesystem order, increase `max_results`
- **Description**: Retrieve more raw hits and rely on the LLM to ignore noise.
- **Pros**: Minimal code change.
- **Cons**: Linear cost in prompt tokens, still biased by walk order, and
  irrelevant evidence remains indistinguishable from relevant evidence.

### Alternative B: BM25 / TF-IDF index over repository text
- **Description**: Build a term-frequency index and rank by statistical
  relevance.
- **Pros**: Better natural-language recall; standard IR scoring.
- **Cons**: Requires index construction and persistence for every pinned
  commit, adds a dependency, and complicates exact-symbol retrieval that
  Bug reports depend on.

### Alternative C: Dense embedding retrieval (pgvector)
- **Description**: Embed code chunks and search by semantic similarity.
- **Pros**: Handles paraphrased symptoms.
- **Cons**: Non-deterministic, expensive, misses exact stack-trace tokens, and
  the specification defers embeddings until a measured retrieval need (Phase 8).

### Alternative D: Exclude all documentation matches
- **Description**: Only return files with source-code extensions.
- **Pros**: Guarantees code-only evidence.
- **Cons**: Changelogs and docs can legitimately explain behavior; hard
  exclusion loses useful context that should be down-ranked, not deleted.

---

## 4. Why the Alternatives Were Not Selected

1. **Alternative A does not solve the defect**: the failure was ordering, not
   quantity. More hits from `HISTORY.md` would still bury the source file.
2. **Alternative B adds infrastructure before measurement**: the build
   specification explicitly starts with a lexical baseline and only adds hybrid
   retrieval after evaluation demonstrates misses.
3. **Alternative C violates the deterministic-first constraint**: exact error
   strings and symbols are the highest-signal inputs in bug reports, and
   embeddings are non-deterministic and costlier to operate.
4. **Alternative D overcorrects**: provenance and relevance are different
   concerns; down-ranking preserves documentation recall while ensuring code
   outranks prose.

---

## 5. Trade-offs and Consequences

### Benefits (Positive Consequences)
- Evidence returned for real-world issues is source-code-first and reproducible.
- One file/term cannot starve the result budget.
- Declared line ranges always describe the excerpt exactly, strengthening the
  citation-validation guarantee in ADR-003.
- No new runtime dependency beyond the existing optional ripgrep binary.

### Drawbacks & Limitations
- The weights are hand-tuned heuristics, not learned or evaluated against a
  labelled relevance set (Phase 7 will provide measurement).
- Prose terms are dropped when three identifiers exist; an issue with many
  identifiers but a vague title may miss useful non-identifier matches.
- Symbol-name bonuses and AST expansion apply to Python only, matching the
  current MVP language scope.

### Trade-offs & Mitigations
- Hard-coded weights are centralized (`term_weight`, `_file_priority`) and
  covered by ranking regression tests, so they can be tuned without touching
  call sites.
- Documentation is down-ranked rather than excluded, mitigating recall loss.
- If evaluation later shows ranking misses, BM25 or hybrid fusion can replace
  the scoring step without changing the `search_code` contract.

---

## 6. Implementation References

- **Primary Implementation**: [`src/issuelens/repository/code_search.py`](../../src/issuelens/repository/code_search.py)
- **Range Integrity**: [`src/issuelens/repository/snippets.py`](../../src/issuelens/repository/snippets.py)
- **Query Construction / Follow-up**: [`src/issuelens/agent/nodes.py`](../../src/issuelens/agent/nodes.py)
- **Validation of Documentation Evidence**: [`src/issuelens/agent/validator.py`](../../src/issuelens/agent/validator.py)
- **Unit Tests**: [`tests/unit/test_code_search.py`](../../tests/unit/test_code_search.py), [`tests/unit/test_graph.py`](../../tests/unit/test_graph.py), [`tests/unit/test_validator.py`](../../tests/unit/test_validator.py)
- **Build Log**: [`docs/BUILD_LOG.md`](../BUILD_LOG.md) Entry 014

---

## 7. Conditions That Would Justify Revisiting the Decision

Revisit this decision if:
- The Phase 7 evaluation benchmark shows source-code precision/recall below the
  agreed relevance rubric on a representative issue set.
- Multi-language support is added and symbol bonuses must become
  language-specific.
- Issue volume or repository size makes per-query scanning (including
  `list_source_files` for path anchoring) a latency bottleneck.
- Hybrid BM25/embedding retrieval is introduced in Phase 8.

---

## 8. Related ADRs

- [ADR-002: LangChain Provider Adapter and Report Generation](ADR-002-llm-provider-adapter-and-report-generation.md): Defines the evidence/report contract this ranking feeds.
- [ADR-003: Deterministic Citation Validation](ADR-003-deterministic-citation-validation-and-insufficient-evidence.md): Depends on excerpt/range integrity guaranteed here.
- [ADR-005: LangGraph Agent Workflow](ADR-005-langgraph-agent-workflow-and-state-machine.md): Calls retrieval nodes and consumes ranked evidence.
