# IssueLens Evaluation Benchmark Suite

This directory contains the benchmark dataset, its documentation, and recorded results for **IssueLens**.

> **Status (2026-11-27):** the harness and metrics were rebuilt after a post-build audit found that the previously published numbers could not have come out differently ([ADR-013](../docs/decisions/ADR-013-evaluation-metric-redesign-and-leak-closure.md)). The first live run with the current code is recorded in [`results.json`](results.json) and summarised in [Section 7](#7-results). The earlier run is kept as [`results.legacy-v1.json`](results.legacy-v1.json) for reference only.

---

## 1. Purpose & Guiding Principles

1. **Reproducibility**: every case pins the pre-fix commit (`target_commit_sha`); results record the IssueLens commit, whether the working tree was dirty, the metrics version and key configuration.
2. **Leak prevention**: the resolving PR, fix commit, issue comments and closed state are withheld from the agent (Section 2).
3. **Metrics that can fail**: every metric is tested with an input that should score badly (Section 4).

---

## 2. Retrospective Leak-Prevention Protocol

If the agent can see the fix (the PR, the post-fix code, or comments discussing it), it can copy the answer instead of investigating.

```text
               +-------------------------------------------+
               | Historical GitHub Bug Report              |
               +-------------------------------------------+
                                     |
               +---------------------+---------------------+
               |                                           |
               v                                           v
    [VISIBLE TO AGENT]                        [WITHHELD]
    - Issue title & body as frozen in         - Resolving PR and fix commit
      cases.jsonl                             - All issue comments
    - Issue presented as "open"               - Closed state / closed_at
    - Pre-fix snapshot (parents[0] of merge)  - expected_files, known_resolution
```

What the live runner actually does ([`run_eval.py`](../src/issuelens/evaluation/run_eval.py)):

| Control | Implementation |
| :--- | :--- |
| Pre-fix snapshot | `create_live_snapshot_resolver()` loads/downloads the archive at `target_commit_sha`; the service is also called with `commit_sha=target_commit_sha`. |
| No fallback to post-fix code | The live runner uses `require_snapshot=True`: if the pinned snapshot cannot be loaded, the case is recorded as `snapshot_unavailable` and the agent is not run. (Before the audit it silently investigated the default branch, which contains the fix.) |
| Comments withheld | `EvaluationGitHubClient.withhold_post_report_information()` sets `comments=[]`. (Before the audit, up to 100 comments reached the prompt and the document index.) |
| Issue text frozen | Title/body come from `cases.jsonl` (`issue_title`, `issue_body`, body truncated to 3,000 characters at collection time) rather than the live issue, which may have been edited after the fix. |
| Closed state hidden | The issue is presented with `state="open"`, `closed_at=None`. |
| PR targets rejected | The GitHub adapter rejects pull-request numbers as investigation targets. |
| Ground truth never passed | `expected_files` and `known_resolution` are only used for scoring (`test_evaluation_runner_withholds_ground_truth_from_agent`). |

Not controlled: the foundation model may have seen these public issues or fixes during pre-training (Section 8).

---

## 3. Benchmark Dataset (`evals/cases.jsonl`)

20 closed bug reports from `BerriAI/litellm`, generated with [`scripts/build_eval_set.py`](../scripts/build_eval_set.py):

```bash
uv run python scripts/build_eval_set.py --output evals/cases.jsonl --limit 20 --max-per-module 6
```

Selection rules as implemented (after the audit):

1. **Candidates**: five keyword searches (`router`, `cache`, `caching`, `budget`, `cost`) over closed `bug` issues, then up to 6 pages of `GET /repos/BerriAI/litellm/issues?state=closed&labels=bug&sort=updated&direction=desc&per_page=100`. Pull requests are filtered out.
2. **Concrete symptom** in the issue body: a traceback or named exception, an HTTP 4xx/5xx, crash/hang, dropped/missing/ignored value, or expected-vs-actual wording (`has_concrete_symptom()`).
3. **Linked merged PR** found via `GET /search/issues?q=repo:BerriAI/litellm+type:pr+is:merged+{issue}` whose title or body uses a GitHub closing keyword (`fixes/closes/resolves #N`). A bare `#N` mention does not count (`pr_closes_issue()`).
4. **Size**: the PR touches 1–3 non-test, non-docs Python files (all pages of `GET /pulls/{n}/files`).
5. **Ground truth** `expected_files`: those files *as they exist at the pre-fix commit*. Files the PR added are excluded, renamed files use their previous name (`pre_fix_expected_files()`).
6. **Pre-fix commit**: `parents[0]` of the PR merge commit, confirmed to exist.

Audit of the committed cases against these rules (2026-11-25, read-only GitHub API calls): all 20 use a closing keyword and pass the symptom rule; CASE-017 and CASE-020 listed a file their fix created (`litellm/llms/sap/chat/reasoning.py`, `litellm/responses/instructions_length.py`, both absent at the pinned commit), which were removed from `expected_files`.

---

## 4. Evaluation Metrics & Targets (metrics version 2)

Implemented in [`metrics.py`](../src/issuelens/evaluation/metrics.py). Quality means are computed over **scored cases** (runs that produced a report); failed runs are reported separately as the completion rate.

| Metric | Computation | Target |
| :--- | :--- | :--- |
| **Root-cause useful rate** | Share of scored cases where a supported hypothesis cites a file the fix changed: `correct` = the top hypothesis does, `partially_useful` = a lower-ranked one does, `incorrect` = none does, `insufficient_evidence` = no hypothesis is supported. | `>= 70%` correct + partial |
| **LLM citation validity** | Of the evidence IDs the LLM cited as support, the share that passed validation (`supporting_evidence_ids` vs `rejected_evidence_ids`). Cases with no citations are excluded and counted separately. | `>= 95%` |
| **Unsupported claims** | Hypotheses per report that the validator could not ground in repository evidence (`support_status != "supported"`). | `< 0.5` per report |
| **Search Recall@k / Hit@k** | Expected files among the top *k* files the **search tools** returned (retrieval order). Measures retrieval, not the diagnosis. | Recall@5 `>= 80%`, Hit@5 `>= 90%` |
| **Diagnosis Hit@k** | Expected file among the top *k* files the **hypotheses cite**, in hypothesis rank order. | reported, no target |
| *Keyword overlap (diagnostic)* | Share of the fix PR title's keywords found in the hypotheses, shown next to the same score for the raw issue text ("issue echo baseline"). | not a quality metric |

All five targets are computed in `benchmark_targets_met`.

Limits of these metrics: file-level alignment says whether the agent pointed at the right code, not whether its explanation is right. An LLM-judge or human rubric for explanations is **not implemented**.

---

## 5. Running the Evaluation Harness

### Live benchmark (`--live`)
Uses the LLM provider and GitHub token configured in `.env` and writes `evals/results.json`:

```bash
uv run python -m issuelens.evaluation.run_eval --live --case-id CASE-001
uv run python -m issuelens.evaluation.run_eval --live            # all 20 cases
uv run python -m issuelens.evaluation.run_eval --live --save-db  # also persist to the database
```

Cost/latency: each case makes 1 to `MAX_RETRIEVAL_ROUNDS` LLM calls (initial hypotheses + one re-evaluation per follow-up round that found new evidence; see [ADR-012](../docs/decisions/ADR-012-llm-hypothesis-reevaluation-loop.md)).

### Mock mode (default, offline): harness check only
```bash
uv run python -m issuelens.evaluation.run_eval --max-cases 3
```
`MockInvestigationService` returns each case's **own ground truth** as the report, so every quality metric is perfect by construction. It checks that loading, metrics, and serialization run end to end; it is **not** a measurement. Output goes to `evals/results.mock.json` (git-ignored) with a warning, so it cannot overwrite live results.

### Options
```bash
--case-id CASE-001        # single case
--max-cases 5             # first N cases
--cases-path PATH         # alternate dataset
--output PATH             # alternate output file
--k-values 1,3,5,10       # ranks for Recall/Hit@k
```

---

## 6. Output Artifact

`evals/results.json` (live) contains:
- Provenance: `mode`, `run_timestamp`, `model_name`, `issuelens_commit_sha`, `issuelens_tree_dirty`, `metrics_version`, `config` (provider, model, rounds, LLM timeout, token cap).
- Aggregates: `total_cases`, `scored_cases`, `completion_rate`, `completed_cases`, `insufficient_evidence_cases`, `failed_cases`, `root_cause_useful_rate`, `mean_citation_validity`, `cases_with_citations`, `mean_unsupported_claims`, `mean_search_recall_at_k`, `mean_search_hit_at_k`, `mean_diagnosis_hit_at_k`, `mean_keyword_overlap`, `mean_issue_echo_keyword_overlap`, `root_cause_distribution`, `benchmark_targets_met`.
- Per-case `case_metrics` and `failure_notes`.

A result is reproducible only if `issuelens_tree_dirty` is `false`.

---

## 7. Results

### Live run, metrics v2: `results.json`
Recorded 2026-11-26 (UTC) with `gpt-4o-mini` (OpenAI), `MAX_RETRIEVAL_ROUNDS=3`, `LLM_TIMEOUT_SECONDS=120`, `max_report_tokens=2500`, on IssueLens commit `fea2660` with a clean tree (`issuelens_tree_dirty: false`). All numbers below are copied from that file.

| Metric | Result | Target | Met |
| :--- | :--- | :--- | :--- |
| Completion (produced a report) | 20 / 20 | — | — |
| Root-cause useful rate | **50%** (10 correct, 0 partial, 4 incorrect, 6 insufficient evidence) | ≥ 70% | No |
| LLM citation validity | 100% over the 17 cases with citations | ≥ 95% | Yes |
| Unsupported claims | 0.35 per report | < 0.5 | Yes |
| Search Recall@5 | 50.8% | ≥ 80% | No |
| Search Hit@5 | 60% | ≥ 90% | No |
| Diagnosis Hit@1 / @5 | 45% / 50% | — | — |
| Mean latency | 130.7 s per case | — | — |
| *Keyword overlap (diagnostic)* | agent 0.33 vs issue-echo baseline 0.71 | — | — |

How to read this:
- **Root cause:** half the cases end with a supported top hypothesis that cites a file the fix changed. The issue-echo baseline cites no files, so it scores 0 on this metric (it would be `insufficient_evidence` everywhere); the keyword-overlap line shows why the old metric was not usable (the echo beats the agent).
- **Retrieval is the main bottleneck.** In the 12 cases where search returned an expected file in its top 5, 8 diagnoses were correct, 3 ended with insufficient evidence and 1 was incorrect. In the 8 cases where it did not, 2 were still correct (found in follow-up rounds), 3 insufficient and 3 incorrect.
- **Search found the file but the diagnosis did not use it** in 4 cases (CASE-005, -007, -019, -020): a prompting/reasoning gap, separate from retrieval.
- **Citation validity 100%** means the LLM never cited an evidence ID that was not shown to it or failed validation. Because the model only selects IDs, this is a weak check (it detects invented IDs, not wrong reasoning); 3 cases cited nothing.
- 6 insufficient-evidence reports are the guardrail working as designed: no hypothesis had repository support, and none was promoted.

With 20 cases from one repository, a difference of 2–3 cases is within noise (Section 8).

### Legacy run (metrics v1, superseded): `results.legacy-v1.json`
Recorded 2026-11-24 with `gpt-4o-mini` and the code before the audit. Its headline numbers (17/20 completed, "100% citation validity on completed cases", "0.00 unsupported claims", "70.6% correct root cause", Recall@5 36.7%) **should not be cited as evidence of quality**:

- **Citation validity 100% / unsupported claims 0.00** could not have been otherwise: the metric re-checked a report the validator had already filtered, the LLM never wrote paths or excerpts, and uncited hypotheses were auto-assigned evidence.
- **Root-cause "correct"** was keyword overlap with the fix PR title. Scoring the raw issue text the same way gives 20/20 "correct", versus the agent's 12/20.
- **Recall@k** scored the lexical search order, not the diagnosis. Recall@3 = Recall@5 was coincidental (no expected file ranked 4th or 5th).
- **Leakage**: issue comments (often discussing the fix) were passed to the agent, and a failed snapshot download would have fallen back to the post-fix default branch.
- **Provenance**: the recorded `issuelens_commit_sha` (`8756e74`) predates the committed dataset and generator; the run came from an uncommitted working tree (only `run_eval.py` differed in `src/`).
- Two cases' ground truth included files that do not exist at the pre-fix commit.

The file is kept unchanged as a historical record. Its field names follow metrics v1.

---

## 8. Limitations & Methodological Caveats

1. **Small, single-repository sample**: 20 cases from `BerriAI/litellm`. Differences of a few cases are noise.
2. **Selection bias**: only bugs fixed by PRs touching 1–3 Python files with a clear symptom.
3. **Pre-training contamination**: the foundation model may have seen these public issues or fixes.
4. **File-level ground truth**: a fix may touch files that are not the root cause (and vice versa); explanation quality is not scored.
5. **Upstream timeouts**: multi-call investigations on a large repository can hit provider timeouts (`LLM_TIMEOUT_SECONDS`, default 120); those cases count against the completion rate, not quality.
