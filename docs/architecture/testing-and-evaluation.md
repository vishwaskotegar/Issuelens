# Testing & Evaluation Architecture

This document details the testing architecture, test boundaries, mocking strategies, and evaluation framework of IssueLens. It covers both the **hermetic automated test suites** (Phases 0–6) and the **retrospective benchmark evaluation harness** (Phase 7).

---

## 1. Testing Pyramid & Verification Architecture

IssueLens enforces a rigorous hermetic testing discipline. Automated tests never depend on live external network connections, paid LLM vendor APIs, or live GitHub endpoints:

```mermaid
flowchart TD
    subgraph ImplementedPyramid["Implemented Automated Testing (Phases 0 - 6)"]
        direction TB
        UnitTests["Unit Tests<br/>Isolated functions, parsers, models, filters, persistence, metrics<br/><code>test_url_parser.py</code>, <code>test_validator.py</code>, <code>test_eval_metrics.py</code>"]
        IntegrationTests["Service Integration Tests<br/>Pipeline integration with hermetic mocks<br/><code>test_investigation_service.py</code>, <code>test_snapshot_cache.py</code>, <code>test_graph.py</code>"]
        APITests["REST API Tests<br/>FastAPI HTTP endpoints via TestClient<br/><code>test_issue_api.py</code>, <code>test_investigation_api.py</code>, <code>test_health.py</code>"]
        APITests --> IntegrationTests
        IntegrationTests --> UnitTests
    end

    subgraph ImplementedEvaluation["Implemented Benchmark Evaluation Harness (Phase 7)"]
        direction TB
        DatasetCurated["Curated Issue Benchmark Dataset<br/>20 real-world public bug reports<br/><code>evals/cases.jsonl</code>"]
        LeakPrevention["Retrospective Leak-Prevention Harness<br/>Withholds fix PRs, comments, closed state<br/>Pins pre-fix commit SHA; no fallback"]
        MetricsEngine["Deterministic Metrics Evaluator (v2)<br/>Root-cause alignment, LLM citation validity, Search/Diagnosis Hit@k<br/><code>src/issuelens/evaluation/metrics.py</code>"]
        DatasetCurated --> LeakPrevention
        LeakPrevention --> MetricsEngine
    end

    ImplementedPyramid -->|"Feeds verified pipeline into"| ImplementedEvaluation
```

---

## 2. Implemented Test Organization & Boundaries

The automated test suite resides in `tests/` and runs offline (no network, no paid LLM). Counts below were generated on 2026-11-25 with `uv run pytest --collect-only -q` (285 tests); re-run that command for current numbers rather than trusting this listing.

```text
tests/
├── conftest.py                              # Global hermetic test database setup and loop policy
├── fixtures/sample_repo/                    # Sample repository snapshot fixture
│   ├── .env                                 # Fake-secret fixture for secret-exclusion tests (tracked)
│   ├── README.md
│   └── sample_pkg/ (auth.py, database.py, broken_syntax.py)
└── unit/
    ├── agent/test_prompt_injection_defense.py   (4)
    ├── test_build_eval_set.py                   (3)  dataset selection rules
    ├── test_code_search.py                      (21)
    ├── test_config.py                           (7)  incl. env/.env list parsing
    ├── test_deployment_hardening.py             (15) auth, rate limits, Retry-After, eviction
    ├── test_document_rag.py                     (11)
    ├── test_eval_dataset.py                     (8)
    ├── test_eval_metrics.py                     (16) metrics v2
    ├── test_eval_runner.py                      (7)
    ├── test_github_client.py                    (20)
    ├── test_github_models.py                    (3)
    ├── test_graph.py                            (16) LangGraph incl. re-evaluation loop
    ├── test_health.py                           (3)
    ├── test_investigation_api.py                (7)  incl. error redaction, 500 headers
    ├── test_investigation_service.py            (6)
    ├── test_issue_api.py                        (8)
    ├── test_leak_prevention.py                  (3)
    ├── test_llm_client.py                       (16)
    ├── test_logging.py                          (4)
    ├── test_persistence.py                      (12) incl. migration/model parity
    ├── test_prompts.py                          (8)
    ├── test_python_symbols.py                   (5)
    ├── test_report_schema.py                    (7)
    ├── test_security_headers.py                 (5)
    ├── test_snapshot.py                         (11)
    ├── test_snapshot_cache.py                   (8)
    ├── test_snippets.py                         (6)
    ├── test_trusted_proxies.py                  (6)
    ├── test_url_parser.py                       (14)
    └── test_validator.py                        (25)
```

Known gaps (not covered by automated tests): the PostgreSQL branch of migration 002, PostgreSQL/JSONB behaviour in general (tests use SQLite), rejected-origin CORS behaviour, and the Docker image build.

### Key Test Boundaries & Test Doubles

1. **GitHub Network Boundary**:
   - In [`tests/unit/test_github_client.py`](../../tests/unit/test_github_client.py), network requests are intercepted using `httpx.MockTransport`. No HTTP sockets are opened; tests provide synthetic responses for `/issues`, `/comments`, `/repos`, `/commits`, and zipball streaming.
   - For higher-level route and service tests, `FakeGitHubClient` implements [`GitHubClientProtocol`](../../src/issuelens/github/client.py), returning predetermined [`GitHubIssueContext`](../../src/issuelens/github/models.py) structures without serialization overhead.
2. **LLM Provider Boundary**:
   - In [`tests/unit/test_llm_client.py`](../../tests/unit/test_llm_client.py) and service tests, LLM interactions are decoupled via [`LLMClientProtocol`](../../src/issuelens/agent/llm.py).
   - Tests inject [`MockLLMClient`](../../src/issuelens/agent/llm.py), which records prompts and returns deterministic, valid (or deliberately malformed) [`InvestigationReportPayload`](../../src/issuelens/agent/report.py) objects.
3. **Repository Filesystem Boundary**:
   - Tests execute against a committed fixture repository at [`tests/fixtures/sample_repo/`](../../tests/fixtures/sample_repo).
   - Includes edge case files: [`broken_syntax.py`](../../tests/fixtures/sample_repo/sample_pkg/broken_syntax.py) (verifies syntax tolerance) and fixture `.env` (verifies secret exclusion).
4. **Database & Persistence Boundary**:
   - In [`tests/unit/test_persistence.py`](../../tests/unit/test_persistence.py), [`tests/unit/test_eval_runner.py`](../../tests/unit/test_eval_runner.py), and across the API test suite via [`tests/conftest.py`](../../tests/conftest.py), persistence operations run against in-memory SQLite (`sqlite+aiosqlite:///:memory:`) using `StaticPool` and `check_same_thread=False`.

---

## 3. Evaluation Architecture (Phase 7)

Phase 7 implements an automated benchmarking harness to measure investigation accuracy, citation validity, and hallucination rates across prompt and model versions.

### 3.1 Retrospective Leak-Proof Evaluation Protocol

When evaluating an AI debugging assistant against historical, resolved bug reports, **data leakage** is the most critical risk:
- If the agent is provided the pull request that resolved the bug, the eventual fix commit, or comments discussing the merged solution, the evaluation is contaminated and produces false results.
- If the agent inspects the repository at the `HEAD` commit (which already includes the fix), the bug will not exist in the inspected code.

#### Leakage Protection Measures:
1. **Withhold Fix Commit, PR, Comments, and Closed State**: PR numbers are rejected as targets; in live mode `EvaluationGitHubClient` removes all issue comments, presents the issue as open, and uses the issue text frozen in `cases.jsonl`. A missing pinned snapshot fails the case (`snapshot_unavailable`) instead of falling back to the default branch. (Comments were passed to the agent until the 2026-11-25 audit.)
2. **Pre-Fix Commit Pinning**: Every evaluation case in `evals/cases.jsonl` explicitly records the `target_commit_sha`: the commit immediately preceding the merged fix. The repository snapshot is pinned strictly to this pre-fix SHA.
3. **Double-Blind Evaluation**: The agent operates strictly with the initial issue description and pre-fix snapshot. The known resolution and expected relevant files are stored exclusively in the evaluation harness as ground truth labels, completely invisible to the agent.

```mermaid
sequenceDiagram
    participant EvalHarness as Evaluation Harness<br/>(run_eval.py)
    participant Agent as IssueLens Agent<br/>(service.py)
    participant Repo as Pre-Fix Snapshot<br/>(Pinned Commit N-1)

    Note over EvalHarness: Load Case from evals/cases.jsonl<br/>(Issue #123, Pre-Fix SHA: 7f8a9b...)
    Note over EvalHarness: WITHHOLD ground truth:<br/>- resolving PR #125<br/>- fix commit 8e9f0a...<br/>- post-fix comments
    
    EvalHarness->>Agent: investigate(issue_url, snapshot=pre_fix_snapshot, commit_sha=pre_fix_sha)
    Agent->>Repo: Bounded Search & AST Retrieval
    Repo-->>Agent: Code Evidence (Pre-Fix State)
    Agent-->>EvalHarness: Generated InvestigationReport
    
    Note over EvalHarness: Compare against ground truth:<br/>1. Do supported hypotheses cite a fixed file? (Root-cause alignment)<br/>2. Did LLM-cited evidence IDs pass validation? (Citation validity)<br/>3. Are hypotheses grounded? (Unsupported claims)<br/>4. Did search return the fixed files? (Search Recall@k)
```

### 3.2 Evaluation Metrics & Computation (metrics version 2)

Definitions and targets are maintained in [`evals/README.md` Section 4](../../evals/README.md#4-evaluation-metrics--targets-metrics-version-2) (single source of truth) and implemented in [`metrics.py`](../../src/issuelens/evaluation/metrics.py). Summary: root-cause useful rate (file-based, `>= 70%`), LLM citation validity over cited IDs (`>= 95%`), unsupported claims (`< 0.5`/report), Search Recall@5 (`>= 80%`) and Hit@5 (`>= 90%`), plus Diagnosis Hit@k and a keyword-overlap diagnostic with an issue-echo baseline. Quality means exclude failed runs, which are reported as the completion rate. See [ADR-013](../decisions/ADR-013-evaluation-metric-redesign-and-leak-closure.md) for why v1 metrics were replaced.

### 3.3 Evaluation Runner CLI & Machine-Readable Output

The benchmark harness is executed via CLI:

```powershell
uv run python -m issuelens.evaluation.run_eval --live            # writes evals/results.json
uv run python -m issuelens.evaluation.run_eval                   # mock harness check -> evals/results.mock.json
```

Outputs include:
1. **Console Summary Table**: Displays total cases, status counts, citation validity rate, Recall@k breakdown, and failure notes.
2. **Machine-Readable JSON Artifact**: `evals/results.json` (live) or `evals/results.mock.json` (mock, git-ignored, carries a warning that its metrics echo ground truth). Includes provenance: commit SHA, dirty-tree flag, metrics version, config.
3. **Database Records**: When `--save-db` is specified, records are persisted to `evaluation_cases` and `evaluation_results` tables.

---

## 4. Developer Quality Verification Commands

To verify code quality, type safety, and test coverage before committing:

```powershell
# 1. Run linter
uv run --offline ruff check .

# 2. Run static type checker
uv run --offline mypy src tests

# 3. Run full automated test suite
uv run --offline pytest

# 4. Run evaluation benchmark harness CLI
uv run --offline python -m issuelens.evaluation.run_eval --max-cases 5
```
