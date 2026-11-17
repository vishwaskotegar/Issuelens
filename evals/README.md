# IssueLens Evaluation Benchmark Suite

This directory contains the curated evaluation benchmark dataset, evaluation documentation, and baseline results for **IssueLens**.

---

## 1. Purpose & Guiding Principles

The credibility of an AI debugging assistant depends on rigorous, reproducible measurement against real-world bug reports—not cherry-picked demos.

IssueLens evaluates performance using a curated benchmark of resolved public GitHub issues under strict experimental controls:
1. **Reproducibility**: Every evaluation case pins an exact pre-fix commit SHA (`target_commit_sha`).
2. **Retrospective Leak Prevention**: Ground truth resolutions, fix pull requests, and solution commits are strictly withheld from the agent.
3. **Deterministic Verification**: Every cited code snippet is verified against physical file bytes in the pinned snapshot.

---

## 2. Retrospective Leak-Prevention Protocol

When testing an agent against historical bug reports, **data contamination (leakage)** can invalidate results. If the agent accesses the resolving pull request, post-fix commits, or issue comments discussing the merged fix, it appears artificially intelligent when it is merely copying the solution.

```text
               +-------------------------------------------+
               | Historical GitHub Bug Report              |
               | (e.g. pallets/flask#4020)                 |
               +-------------------------------------------+
                                     |
               +---------------------+---------------------+
               |                                           |
               v                                           v
    [VISIBLE TO AGENT]                        [WITHHELD GROUND TRUTH]
    - Issue title & description               - Resolving Pull Request (#4025)
    - Pinned Pre-Fix Snapshot (Commit N-1)    - Bugfix Commit SHA
    - Codebase in broken state                - Post-fix discussion & PR comments
                                              - Expected files & Known resolution
```

### Leakage Defense Rules:
- **Pre-Fix Commit Pinning**: The repository snapshot provided to the investigation service is checked out strictly at `target_commit_sha` (the parent commit of the resolving pull request).
- **Withholding Solution Artifacts**: The GitHub adapter suppresses comments that link to the resolving PR or commit SHA.
- **Double-Blind Scoring**: Ground truth labels (`expected_files` and `known_resolution`) are kept exclusively in the evaluation harness and are never passed to the agent or injected into prompts.

---

## 3. Benchmark Dataset (`evals/cases.jsonl`)

The dataset contains 20 curated public Python bugs across prominent open-source libraries:

| Case ID | Repository | Issue | Target Pre-Fix SHA | Known Resolution Summary |
| :--- | :--- | :--- | :--- | :--- |
| `CASE-001` | `pallets/flask` | #4020 | `d4a1b8e4f...` | Nested blueprint url_prefix concatenation with empty parent |
| `CASE-002` | `pallets/flask` | #3950 | `c3b2a1f0e...` | Custom JSONEncoder kwargs forwarding in `flask.json.dump` |
| `CASE-003` | `psf/requests` | #5201 | `b2c3d4e5f...` | Preserve Content-Type header on 303 redirect |
| `CASE-004` | `psf/requests` | #4820 | `a1b2c3d4e...` | Chunked encoding generator uploads with zero-byte chunks |
| `CASE-005` | `encode/httpx` | #1220 | `f1e2d3c4b...` | Base URL trailing slash handling when joining relative paths |
| `CASE-006` | `encode/httpx` | #1405 | `e9d8c7b6a...` | Duplicate Proxy-Authorization headers on HTTPS CONNECT tunnels |
| `CASE-007` | `pytest-dev/pytest` | #8410 | `123456789...` | Capture manager lifecycle teardown during `pytest_unconfigure` |
| `CASE-008` | `pytest-dev/pytest` | #7920 | `abcdef012...` | Async class-scoped fixtures combined with `@pytest.mark.parametrize` |
| `CASE-009` | `pydantic/pydantic` | #3100 | `7f8e9d0a1...` | `Field(default_factory=...)` mutation on subclassed models |
| `CASE-010` | `pydantic/pydantic` | #2850 | `9a8b7c6d5...` | OpenAPI schema generation for `Union[dict, list]` |
| `CASE-011` | `tiangolo/fastapi` | #4500 | `3b4c5d6e7...` | Propagate `request.state` to sub-applications mounted via `app.mount` |
| `CASE-012` | `tiangolo/fastapi` | #3810 | `5c6d7e8f9...` | Custom exception headers serialization in JSONResponse |
| `CASE-013` | `pallets/werkzeug` | #2200 | `8d9e0f1a2...` | Multiple Set-Cookie header handling without overwrite |
| `CASE-014` | `pallets/jinja` | #1510 | `0e1f2a3b4...` | Async `loop.cycle()` generator execution in native environment |
| `CASE-015` | `pallets/click` | #2105 | `4a5b6c7d8...` | CLI argument parser order with negative flag options |
| `CASE-016` | `urllib3/urllib3` | #2400 | `6e7f8a9b0...` | SSLEOFError handling during idle connection pool recycling |
| `CASE-017` | `textualize/rich` | #1890 | `1a2b3c4d5...` | Table column width measuring with ANSI escape sequences |
| `CASE-018` | `marshmallow-code/marshmallow` | #1920 | `2b3c4d5e6...` | `allow_none=True` distinction from missing default value |
| `CASE-019` | `psf/black` | #2600 | `3c4d5e6f7...` | Preserve magic trailing commas in generator expressions |
| `CASE-020` | `sample/repo` | #101 | `012345678...` | Empty token validation in TokenAuthenticator (Hermetic fixture) |

---

## 4. Evaluation Metrics & Targets

IssueLens measures five primary quality metrics:

| Metric | Computation | Target Benchmark |
| :--- | :--- | :--- |
| **Citation Validity Rate** | $\frac{\text{valid code citations}}{\text{total code citations}}$ verified against snapshot bytes | $\ge 95\%$ |
| **Retrieval Recall@k** | $\frac{|\text{retrieved files} \cap \text{expected files}|}{|\text{expected files}|}$ at rank $k \in \{1, 3, 5\}$ | $\ge 80\%$ at $k=5$ |
| **Retrieval Hit@k** | $1.0$ if at least one expected buggy file is in top $k$, else $0.0$ | $\ge 90\%$ at $k=5$ |
| **Unsupported Claims** | Count of hypotheses with empty citations or dangling evidence IDs | $< 0.5$ per report |
| **Root-Cause Alignment** | Heuristic rubric classification: `correct`, `partially_useful`, `incorrect` | $\ge 70\%$ correct/partial |

---

## 5. Running the Evaluation Harness

### Basic Run
Run the full benchmark suite offline and save results to `evals/results.json`:

```bash
uv run --offline python -m issuelens.evaluation.run_eval
```

### Common CLI Options

```bash
# Evaluate a single case
uv run --offline python -m issuelens.evaluation.run_eval --case-id CASE-001

# Limit to first N cases
uv run --offline python -m issuelens.evaluation.run_eval --max-cases 5

# Custom input dataset and output report path
uv run --offline python -m issuelens.evaluation.run_eval --cases-path evals/cases.jsonl --output evals/my_results.json

# Persist evaluation runs to PostgreSQL / SQLite database
uv run --offline python -m issuelens.evaluation.run_eval --save-db

# Custom Recall@k rank cutoffs
uv run --offline python -m issuelens.evaluation.run_eval --k-values 1,3,5,10
```

---

## 6. Output Artifact (`evals/results.json`)

The evaluation runner produces a structured JSON artifact containing:
- `total_cases`, `completed_cases`, `failed_cases`, `insufficient_evidence_cases`
- `mean_citation_validity`, `mean_recall_at_k`, `mean_hit_at_k`
- `mean_unsupported_claims`, `mean_latency_seconds`
- `root_cause_distribution`
- `benchmark_targets_met`: dictionary indicating whether the run met the acceptance criteria
- Per-case diagnostic records (`case_metrics`)
- `failure_notes` highlighting regressions or invalid citations
