# ADR-008: Evaluation Harness and Retrospective Benchmark Architecture

- **Status**: Accepted
- **Date**: 2026-11-18
- **Deciders**: IssueLens Maintainers & Architecture Review Team
- **Phase**: Phase 7: Evaluation Harness
- **Nature of Rationale**: Original

---

## 1. Context and Problem

The engineering credibility of an AI debugging assistant hinges on quantitative, reproducible performance measurement against real-world bug reports rather than cherry-picked, qualitative demonstrations. Section 9 and US-08 of [`docs/BUILD_SPECIFICATION.md`](../BUILD_SPECIFICATION.md#L304-L331) establish that IssueLens must measure citation validity, retrieval recall, and unsupported claim rates across model versions.

However, evaluating LLM-based assistants against historical GitHub issues introduces critical failure modes:
1. **Retrospective Data Contamination (Leakage)**: If an agent has access to the pull request that resolved the bug, the merged fix commit, or subsequent issue comments discussing the resolution, the evaluation becomes contaminated. The agent appears artificially insightful while merely transcribing the solution.
2. **Snapshot Inconsistency**: If an agent inspects the repository at `HEAD`, the investigated bug is already fixed, making accurate retrieval recall impossible to measure.
3. **Flaky & Costly Live Evaluations**: Automated evaluation loops must not depend on live GitHub API availability or paid LLM tokens during unit testing and CI verification.
4. **Lack of Provenance Validation**: Evaluating claims without checking citations against raw bytes allows hallucinated line numbers to pass undetected.

---

## 2. Decision and Rationale

We commit to a **deterministic, leak-proof evaluation harness** with the following architectural components:

1. **Curated Pre-Fix Benchmark Dataset (`evals/cases.jsonl`)**:
   - Curated benchmark containing 20 diverse public Python bug reports (`pallets/flask`, `psf/requests`, `encode/httpx`, `pytest-dev/pytest`, `pydantic/pydantic`, `tiangolo/fastapi`, `pallets/werkzeug`, etc.).
   - Every case strictly defines `target_commit_sha`—the commit immediately preceding the merged fix pull request.
   - Ground truth labels (`expected_files` and `known_resolution`) are stored exclusively in the evaluation harness and never supplied to the agent.
2. **Double-Blind Retrospective Leak-Prevention Protocol**:
   - The evaluation runner suppresses resolving pull request metadata, fix commits, and solution comments.
   - The repository snapshot is pinned strictly to `target_commit_sha`.
3. **Deterministic Multi-Metric Evaluator (`src/issuelens/evaluation/metrics.py`)**:
   - **Citation Validity Rate**: Fraction of code citations whose line ranges exist and whose excerpts match physical bytes in the pinned snapshot (`>= 95%` benchmark gate).
   - **Retrieval Recall@k and Hit@k**: Ranked retrieval coverage of known ground truth files (`>= 80%` at $k=5$).
   - **Unsupported Claims Count**: Number of hypotheses with missing or dangling evidence IDs (`< 0.5` per report).
   - **Root-Cause Alignment**: Heuristic token-overlap and concept alignment between hypotheses and known resolution.
4. **Decoupled Evaluation Runner CLI (`src/issuelens/evaluation/run_eval.py`)**:
   - Dependency-injected runner supporting mock services for hermetic offline tests and live services for benchmarking.
   - Dual emission: structured human-readable ASCII terminal summary and machine-readable JSON artifact (`evals/results.json`).
   - Optional relational persistence to PostgreSQL / SQLite via `EvaluationCase` and `EvaluationResult` ORM records.

---

## 3. Alternatives Considered

### Alternative A: LLM-as-a-Judge Evaluation (Self-Rating)
- **Description**: Use a larger LLM (e.g. GPT-4o or Claude 3.5 Sonnet) to inspect the generated report and grade its accuracy from 1 to 5.
- **Pros**: Easy to implement; requires less ground-truth curation.
- **Cons**: Prone to self-enhancement bias, non-deterministic drift, expensive API costs, and inability to catch physical citation byte misalignments.

### Alternative B: SWE-bench Integration (Execution-Based Eval)
- **Description**: Use the SWE-bench framework with Docker containers to execute repo test suites before and after generated patches.
- **Pros**: Direct measurement of code patch correctness.
- **Cons**: Violates the core safety boundary of IssueLens: **never execute untrusted investigated repository code or install untrusted dependencies**. Furthermore, IssueLens generates diagnostic investigation reports with citations, not autonomous code patches.

---

## 4. Why the Alternatives Were Not Selected

1. **LLM-as-a-Judge Rejection**: As stated in Section 9 of the Build Specification, *"Do not treat LLM self-rating as ground truth."* Subjective judge scores cannot verify whether a file path or line number existed at a historical commit SHA. Deterministic byte-level citation validation and mathematical Recall@k provide unassailable ground truth.
2. **SWE-bench Rejection**: Running untrusted repository code in developer environments introduces high container breakout and remote code execution (RCE) risks. IssueLens is intentionally a read-only, evidence-backed diagnostic assistant.

---

## 5. Trade-offs and Consequences

### Benefits
- **Zero Data Leakage**: Pins pre-fix commits and prevents retrospective information contamination.
- **Hermetic Testability**: The evaluation harness runs in milliseconds offline using test doubles and fixture repositories without spending LLM tokens.
- **Regression Detection**: Quantitative metric gates (`citation_validity >= 0.95`, `recall@5 >= 0.80`, `unsupported_claims < 0.5`) immediately catch prompt or retrieval regressions.
- **Machine-Readable Outputs**: Generates `evals/results.json` suitable for CI artifact tracking and long-term trend analysis.

### Drawbacks & Limitations
- **Curated Dataset Maintenance**: Adding new benchmark cases requires manual inspection of resolved issues, extracting pre-fix commit SHAs, and recording expected files.
- **Heuristic Root-Cause Scoring**: Lexical and token-based alignment against known resolutions is a proxy for human understanding; nuanced semantic matches may score as partial matches.

### Trade-offs & Mitigations
- **Benchmark Size vs. Maintenance**: We start with 20 high-quality, verified cases across diverse Python libraries and provide schema validation to make adding new cases safe and error-free.
- **Offline vs. Live Runs**: Unit tests verify the harness logic hermetically using fixture repositories; benchmark runs against live snapshots can be executed on demand using the CLI.

---

## 6. Implementation References

- Dataset Models & Loader: [`src/issuelens/evaluation/dataset.py`](file:///c:/Projects/Issuelens/src/issuelens/evaluation/dataset.py)
- Metrics Engine: [`src/issuelens/evaluation/metrics.py`](file:///c:/Projects/Issuelens/src/issuelens/evaluation/metrics.py)
- Runner & CLI: [`src/issuelens/evaluation/run_eval.py`](file:///c:/Projects/Issuelens/src/issuelens/evaluation/run_eval.py)
- Benchmark Cases: [`evals/cases.jsonl`](file:///c:/Projects/Issuelens/evals/cases.jsonl)
- Benchmark Guide: [`evals/README.md`](file:///c:/Projects/Issuelens/evals/README.md)
- Unit Test Suites: [`tests/unit/test_eval_dataset.py`](file:///c:/Projects/Issuelens/tests/unit/test_eval_dataset.py), [`tests/unit/test_eval_metrics.py`](file:///c:/Projects/Issuelens/tests/unit/test_eval_metrics.py), [`tests/unit/test_eval_runner.py`](file:///c:/Projects/Issuelens/tests/unit/test_eval_runner.py)

---

## 7. Conditions for Revisiting

This decision should be revisited if:
1. IssueLens adds an optional, isolated container sandbox environment allowing controlled execution of reproduction tests without security risks.
2. Embedding-based semantic similarity models become sufficiently deterministic and fast to serve as an offline root-cause grading component.

---

## 8. Related Decisions

- [`ADR-003: Deterministic Evidence Citation Validation and Insufficient-Evidence Guardrails`](ADR-003-deterministic-citation-validation-and-insufficient-evidence.md)
- [`ADR-004: Commit-Pinned Repository Snapshot Disk Cache with Bounded LRU Eviction`](ADR-004-commit-pinned-snapshot-cache-and-disk-bounds.md)
- [`ADR-007: Relational PostgreSQL Persistence with SQLAlchemy 2.0 and Alembic Migrations`](ADR-007-postgresql-persistence-and-alembic-migrations.md)
