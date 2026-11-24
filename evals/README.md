# IssueLens Evaluation Benchmark Suite

This directory contains the curated evaluation benchmark dataset, evaluation documentation, and baseline results for **IssueLens**.

---

## 1. Purpose & Guiding Principles

The IssueLens evaluation framework measures investigation quality against real-world bug reports under defined experimental controls:
1. **Reproducibility**: Every evaluation case pins an exact pre-fix commit SHA (`target_commit_sha`).
2. **Retrospective Leak Prevention**: Ground truth resolutions, fix pull requests, and solution commits are withheld from the agent.
3. **Deterministic Verification**: Every cited code snippet is verified against physical file bytes in the pinned snapshot.

---

## 2. Retrospective Leak-Prevention Protocol

When testing an agent against historical bug reports, data leakage can invalidate results. If the agent accesses the resolving pull request, post-fix commits, or issue comments discussing the merged fix, it can copy the solution rather than investigating the issue from observed symptoms.

```text
               +-------------------------------------------+
               | Historical GitHub Bug Report              |
               | (e.g. BerriAI/litellm#...)                |
               +-------------------------------------------+
                                     |
               +---------------------+---------------------+
               |                                           |
               v                                           v
    [VISIBLE TO AGENT]                        [WITHHELD GROUND TRUTH]
    - Issue title & description               - Resolving Pull Request
    - Pinned Pre-Fix Snapshot (Commit N-1)    - Bugfix Commit SHA
    - Codebase in broken state                - Expected files & Known resolution
```

### Leakage Defense Rules:
- **Pre-Fix Commit Pinning**: The repository snapshot provided to the investigation service is checked out strictly at `target_commit_sha` (the parent commit of the resolving pull request).
- **PR Target Rejection**: The GitHub adapter validates that target inputs are issues and rejects pull request objects.
- **Double-Blind Scoring**: Ground truth labels (`expected_files` and `known_resolution`) are kept exclusively in the evaluation harness and are never passed to the agent or injected into prompts.

---

## 3. Benchmark Dataset (`evals/cases.jsonl`)

The initial benchmark suite comprised 20 cases: 19 public GitHub issues across open-source libraries and 1 local fixture (`sample/repo`).

The benchmark is refreshed to draw exclusively from real, closed bug reports in `BerriAI/litellm`. Cases are generated using `scripts/build_eval_set.py` via the GitHub API, applying strict qualification criteria:
1. Issue is in `BerriAI/litellm`, closed, and labeled `bug`.
2. A merged pull request links to the issue with resolution keywords (`fixes #N`, `closes #N`, `resolves #N`).
3. The resolving PR modifies 1 to 3 non-test, non-documentation Python files.
4. The issue body describes a concrete error message, crash, or unexpected behavior.
5. The `target_commit_sha` is pinned to the immediate parent of the PR merge commit (`parents[0]["sha"]`).

### Dataset Generation Procedure & Exact Queries

The dataset is generated via the GitHub REST API using `scripts/build_eval_set.py`:

```bash
uv run python scripts/build_eval_set.py --output evals/cases.jsonl --limit 20 --max-per-module 6
```

**Exact GitHub API Queries:**
1. **Candidate Bug Issues**:
   `GET https://api.github.com/repos/BerriAI/litellm/issues?state=closed&labels=bug&sort=updated&direction=desc&per_page=100`
   Filters out pull request items (`"pull_request" not in item`) and confirms that the issue description contains concrete failure symptoms (traceback, exception, crash, error message).
2. **Linked Merged PR Discovery**:
   `GET https://api.github.com/search/issues?q=repo:BerriAI/litellm+type:pr+is:merged+{issue_number}`
   Verifies closing keyword patterns in PR body and commits.
3. **Changed File Bounds**:
   `GET https://api.github.com/repos/BerriAI/litellm/pulls/{pr_number}/files`
   Filters out tests, docs, and configs, requiring 1 to 3 non-test Python files as `expected_files`.
4. **Pre-Fix Target Commit Pinning**:
   `GET https://api.github.com/repos/BerriAI/litellm/commits/{pr.merge_commit_sha}` -> extracts `parents[0]["sha"]` as `target_commit_sha`.
   `GET https://api.github.com/repos/BerriAI/litellm/commits/{target_commit_sha}` -> confirms the pre-fix snapshot commit exists (HTTP 200).

---

## 4. Evaluation Metrics & Targets

IssueLens measures five primary quality metrics:

| Metric | Computation | Target Benchmark |
| :--- | :--- | :--- |
| **Citation Validity Rate** | $\frac{\text{valid code citations}}{\text{total code citations}}$ verified against snapshot bytes | $\ge 95\%$ |
| **Retrieval Recall@k** | $\frac{\lvert\text{retrieved files} \cap \text{expected files}\rvert}{\lvert\text{expected files}\rvert}$ at rank $k \in \{1, 3, 5\}$ | $\ge 80\%$ at $k=5$ |
| **Retrieval Hit@k** | $1.0$ if at least one expected buggy file is in top $k$, else $0.0$ | $\ge 90\%$ at $k=5$ |
| **Unsupported Claims** | Count of hypotheses with empty citations or dangling evidence IDs | $< 0.5$ per report |
| **Root-Cause Alignment** | Heuristic rubric classification: `correct`, `partially_useful`, `incorrect` | $\ge 70\%$ correct/partial |

---

## 5. Running the Evaluation Harness

### 1. Offline Hermetic Benchmark Run (Default)
Run the benchmark suite offline using hermetic test doubles and local fixtures. This verifies metric calculations, citation checks, and benchmark target gates in milliseconds with zero API costs:

```bash
# Run all 20 cases in offline mock mode (default)
uv run python -m issuelens.evaluation.run_eval

# Limit to first 3 cases
uv run python -m issuelens.evaluation.run_eval --max-cases 3
```

### 2. Live Agent Benchmark Run (`--live`)
Execute full LangGraph agent investigations against real repositories at pre-fix commit SHAs using the LLM provider and GitHub token configured in `.env`:

```bash
# Evaluate a single case (e.g. local fixture case CASE-020 or public case CASE-001)
uv run python -m issuelens.evaluation.run_eval --live --case-id CASE-020

# Evaluate a small live batch with database persistence
uv run python -m issuelens.evaluation.run_eval --live --max-cases 2 --save-db
```

### 3. Common CLI Options

```bash
# Evaluate a specific case ID
uv run python -m issuelens.evaluation.run_eval --case-id CASE-001

# Limit to first N cases
uv run python -m issuelens.evaluation.run_eval --max-cases 5

# Custom input dataset and output report path
uv run python -m issuelens.evaluation.run_eval --cases-path evals/cases.jsonl --output evals/my_results.json

# Persist evaluation runs to PostgreSQL / SQLite database
uv run python -m issuelens.evaluation.run_eval --save-db

# Custom Recall@k rank cutoffs
uv run python -m issuelens.evaluation.run_eval --k-values 1,3,5,10
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

---

## 7. Baseline Evaluation Results

The live evaluation suite was executed across all 20 `BerriAI/litellm` benchmark cases using live GitHub API retrieval, commit-pinned local snapshots, and OpenAI `gpt-4o-mini`. Complete unedited results are persisted in [`evals/results.json`](results.json).

### Execution Metadata
- **Execution Mode**: `live`
- **Run Timestamp**: `2026-11-24T10:05:11.594394+00:00`
- **Model**: `gpt-4o-mini`
- **IssueLens Commit SHA**: `8756e7491bd09fe942a43fc8446259c80a26f7a0`
- **Total Cases**: 20
- **Completed Cases**: 17 (85.0%)
- **Failed Cases**: 3 (15.0% due to upstream OpenAI API request timeouts)
- **Mean Case Latency**: 149.35s

### Measured Performance vs Targets

| Metric | Measured Baseline (20 Cases) | Benchmark Target | Target Status |
| :--- | :--- | :--- | :--- |
| **Citation Validity Rate** | **85.0%** (100.0% on completed cases) | $\ge 95\%$ | **FAIL** (Overall mean lowered by 3 API timeouts; 0 invalid citations on completed cases) |
| **Unsupported Claims** | **0.00** per report | $< 0.50$ | **PASS** (Zero hallucinated citations or ungrounded claims) |
| **Retrieval Recall@1** | **24.2%** | — | — |
| **Retrieval Recall@3** | **36.7%** | — | — |
| **Retrieval Recall@5** | **36.7%** | $\ge 80\%$ | **FAIL** (Reflects search complexity across 244MB codebase) |
| **Retrieval Recall@10** | **41.7%** | — | — |
| **Retrieval Hit@1** | **30.0%** | — | — |
| **Retrieval Hit@3** | **45.0%** | — | — |
| **Retrieval Hit@5** | **45.0%** | $\ge 90\%$ | **FAIL** |
| **Retrieval Hit@10** | **50.0%** | — | — |

### Root-Cause Alignment Distribution
- **Correct**: 12 cases (60.0% of total, 70.6% of completed cases)
- **Partially Useful**: 4 cases (20.0% of total, 23.5% of completed cases)
- **Incorrect**: 4 cases (20.0% of total, including 3 API timeouts + CASE-009)

### Failed Cases Breakdown
Three cases failed due to upstream LLM request timeouts during complex cyclic investigation rounds:
- **CASE-006** (`BerriAI/litellm#38816`): `LLM request timed out: Request timed out.`
- **CASE-013** (`BerriAI/litellm#48729`): `LLM request timed out: Request timed out.`
- **CASE-019** (`BerriAI/litellm#24659`): `LLM request timed out: Request timed out.`

---

## 8. Limitations & Methodological Caveats

1. **Dataset Scope**: The benchmark suite comprises 20 cases derived from a single repository (`BerriAI/litellm`). While covering multiple submodules (routing, proxy, caching, providers, and spend tracking), 20 cases form a small sample size that may not capture all real-world bug archetypes.
2. **Selection Criteria**: Cases were filtered to closed bug reports with merged pull requests modifying 1 to 3 non-test, non-doc Python files and containing concrete symptom descriptions. Bugs spanning many files, architectural rewrites, documentation-only issues, or non-reproducible race conditions are excluded from this benchmark.
3. **Potential Pre-training Data Contamination**: `BerriAI/litellm` is a widely known public repository. Although strict retrospective leak-prevention mechanisms withhold the resolving pull request, fix commit, and solution comments from the agent, the underlying foundation model (`gpt-4o-mini`) may have encountered aspects of these issues or discussions in its pre-training corpus. Results on private codebases or newly submitted issues may differ.
4. **Upstream LLM Timeout Sensitivity**: Multi-round cyclic investigations on large codebases involve multiple LLM invocations and long contexts, which can be vulnerable to transient API timeouts without exponential backoff retry tiers across separate provider endpoints.

