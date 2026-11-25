# ADR-013: Evaluation Metric Redesign and Leak Closure

- **Status**: Accepted
- **Date**: 2026-11-25
- **Deciders**: Project owner; post-build audit
- **Phase**: Post-build audit remediation (amends Phase 7 / [ADR-008](ADR-008-evaluation-harness-and-retrospective-benchmark.md))
- **Nature of Rationale**: Original

---

## 1. Context and Problem

The published benchmark (`gpt-4o-mini`, 20 litellm cases) reported 100% citation validity on completed cases, 0.00 unsupported claims, and 70.6% correct root-cause diagnoses. An audit showed:

1. **Citation validity was tautological.** `compute_citation_validity()` re-validated the report *after* the validator had removed invalid evidence, and the LLM never writes paths, lines, or excerpts (only evidence IDs).
2. **Unsupported claims were hidden.** `form_hypotheses()` attached evidence to every uncited hypothesis (fixed in the evidence-integrity branch; see ADR-003 amendment).
3. **Root-cause alignment rewarded wording.** It measured keyword overlap between the hypotheses and the fix PR title. Scoring the raw issue title + body the same way gives 20/20 "correct" (title alone: 15 correct, 5 partial), versus the agent's 12/4/4.
4. **Recall@k scored the search tool**, not the diagnosis.
5. **Failed runs were averaged in as 0%** citation validity (giving the 85% headline), and only 3 of the 5 documented targets were computed.
6. **Leaks**: up to 100 issue comments reached the prompt and document index; a failed snapshot download silently fell back to the default branch (which contains the fix); the prompt told the model the issue was closed.
7. **Mock mode was circular** (it returns the case's own ground truth) and the documented default command overwrote the committed live results.
8. **Provenance**: the recorded commit SHA did not correspond to the code that ran; no dirty-tree flag existed.

---

## 2. Decision and Rationale

**Metrics (version 2)**, in [`metrics.py`](../../src/issuelens/evaluation/metrics.py):
- *LLM citation validity*: share of evidence IDs the LLM cited that passed validation. The validator now records removed IDs in `Hypothesis.rejected_evidence_ids`. No citations → `None`, not 100%.
- *Unsupported claims*: hypotheses with validator `support_status != "supported"`.
- *Root-cause alignment*: file-based (a supported hypothesis cites a file the fix changed; top-ranked = `correct`, lower = `partially_useful`).
- *Search Recall/Hit@k* (retrieval) and *Diagnosis Hit@k* (cited files) reported separately.
- *Keyword overlap* kept as a diagnostic beside an issue-echo baseline.
- Quality means over scored cases; failures reported as `completion_rate`; all five targets computed.

**Leak closure**, in [`run_eval.py`](../../src/issuelens/evaluation/run_eval.py): comments withheld; frozen issue title/body from `cases.jsonl`; issue presented as open; `require_snapshot=True` in live mode (`snapshot_unavailable` instead of fallback); pre-fix SHA passed explicitly.

**Hygiene**: mock output to `evals/results.mock.json` (git-ignored, with a warning); provenance records `issuelens_tree_dirty`, `metrics_version`, and key config; the old live run is preserved unchanged as `results.legacy-v1.json` and not cited as evidence.

**Dataset**: builder requires GitHub closing keywords, paginates PR files, excludes files the fix added, and uses a concrete-symptom filter. Two committed cases had such files removed from `expected_files`.

---

## 3. Alternatives Considered

### Alternative A: Keep v1 metrics and add caveats
- **Pros**: Comparable with the published run.
- **Cons**: The numbers carry no information; caveats do not fix that.

### Alternative B: LLM-as-a-judge for root cause
- **Pros**: Can assess explanations, not just files.
- **Cons**: Paid, run-to-run variance, must use a different model than the agent. Deferred, not rejected.

---

## 4. Why the Alternatives Were Not Selected

1. **A** would keep publishing numbers that cannot fail.
2. **B** adds cost and variance; file-level alignment is a cheap, objective first step. A judge can be layered on later.

---

## 5. Trade-offs and Consequences

### Benefits
- Every metric has a test showing an input that makes it score badly.
- Results distinguish reliability (completion) from quality.

### Drawbacks & Limitations
- Results are not comparable with the legacy run; a new live run is required before any quality claim is made.
- File-level alignment cannot tell a right file with a wrong explanation from a correct diagnosis.
- Withholding all comments also removes legitimate reproduction details reporters add later; this makes the benchmark slightly harder than real use.

### Trade-offs & Mitigations
- Comment withholding is deliberately conservative; a later refinement could keep comments created before the fix PR was opened.

---

## 6. Implementation References

- [`src/issuelens/evaluation/metrics.py`](../../src/issuelens/evaluation/metrics.py), [`src/issuelens/evaluation/run_eval.py`](../../src/issuelens/evaluation/run_eval.py), [`scripts/build_eval_set.py`](../../scripts/build_eval_set.py)
- [`src/issuelens/agent/validator.py`](../../src/issuelens/agent/validator.py) (`support_status`, `rejected_evidence_ids`)
- Tests: [`test_eval_metrics.py`](../../tests/unit/test_eval_metrics.py), [`test_eval_runner.py`](../../tests/unit/test_eval_runner.py), [`test_leak_prevention.py`](../../tests/unit/test_leak_prevention.py), [`test_build_eval_set.py`](../../tests/unit/test_build_eval_set.py)

---

## 7. Conditions That Would Justify Revisiting the Decision

Revisit this decision if:
- An LLM-judge or human rubric is added for explanation quality.
- The dataset grows beyond one repository (per-repository breakdowns become necessary).
- Comment withholding proves too strict (switch to pre-fix-timestamp filtering).

---

## 8. Related ADRs

- [ADR-008: Evaluation Harness](ADR-008-evaluation-harness-and-retrospective-benchmark.md): Amended by this ADR.
- [ADR-003: Deterministic Citation Validation](ADR-003-deterministic-citation-validation-and-insufficient-evidence.md): Supplies `support_status` / `rejected_evidence_ids`.
- [ADR-012: LLM Hypothesis Re-evaluation](ADR-012-llm-hypothesis-reevaluation-loop.md): Changes the number of LLM calls per case.
