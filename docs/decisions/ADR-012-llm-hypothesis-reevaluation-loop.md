# ADR-012: LLM Hypothesis Re-evaluation in the Follow-up Retrieval Loop

- **Status**: Accepted
- **Date**: 2026-11-25
- **Deciders**: Project owner; post-build audit
- **Phase**: Post-build audit remediation (amends Phase 5 / [ADR-005](ADR-005-langgraph-agent-workflow-and-state-machine.md))
- **Nature of Rationale**: Original

---

## 1. Context and Problem

ADR-005 and the README described a "multi-round cyclic agent" that confirms or refutes hypotheses through follow-up retrieval. An audit of [`src/issuelens/agent/nodes.py`](../../src/issuelens/agent/nodes.py) found that:

1. The LLM was called exactly once (`form_hypotheses`), before the loop. Follow-up evidence was appended to the report but never shown to the model, so hypotheses could not change.
2. `evaluate_evidence` rebuilt `followup_queries` from the unchanged `missing_information` every round, undoing the `pop(0)` in `retrieve_followup`; rounds 2 and 3 re-ran the first query.
3. `support_status` and `stop_reason` were written but never survived to the report.

The loop therefore cost latency without being able to change the outcome. The project owner chose to make the loop real rather than relabel it as single-pass.

---

## 2. Decision and Rationale

Add a `reevaluate_hypotheses` node so the graph becomes:

`retrieve_followup → reevaluate_hypotheses → evaluate_evidence → decide_next_step`

- **Re-call the LLM only when there is something new.** If the follow-up round added no evidence (`new_evidence_ids` empty), the node is recorded as `skipped` and no LLM call is made.
- **Same output schema.** Re-evaluation returns `InvestigationReportPayload`, so the citation validator and report builder are unchanged. The prompt ([`build_reevaluation_messages()`](../../src/issuelens/agent/prompts.py)) shows prior hypotheses in a sanitized `<prior_hypotheses>` enclave and marks new evidence `[NEW THIS ROUND]`.
- **Queries are consumed once.** `consumed_queries` in [`InvestigationState`](../../src/issuelens/agent/state.py) is filtered out when the queue is rebuilt.
- **Explicit stop reasons** recorded in state: `loop_cap`, `no_followup_queries`, `hypotheses_converged` (revision identical to the previous hypotheses), `reevaluation_failed`.
- **A failed re-evaluation keeps the prior hypotheses** and stops the loop, instead of failing the run: the first LLM answer is still valid output.
- **Support status is computed by the validator**, not the LLM (`Hypothesis.support_status` is excluded from the LLM JSON schema).
- The loop cap now comes from `MAX_RETRIEVAL_ROUNDS` rather than a hard-coded `3`.

---

## 3. Alternatives Considered

### Alternative A: Relabel as single-pass + follow-up retrieval
- **Pros**: No extra LLM cost; smallest change.
- **Cons**: Follow-up evidence still could not influence the diagnosis; the loop would remain mostly cost.

### Alternative B: Autonomous tool-calling agent (ReAct)
- **Pros**: The model chooses what to search for.
- **Cons**: Unbounded and harder to test; tool arguments become an injection surface. Rejected in ADR-005 for the same reasons.

---

## 4. Why the Alternatives Were Not Selected

1. **Alternative A** keeps the code honest but not useful; the owner explicitly asked for real re-evaluation.
2. **Alternative B** gives up the deterministic, testable bounds that are a core safety property of IssueLens.

---

## 5. Trade-offs and Consequences

### Benefits
- Follow-up evidence can now confirm, revise, or drop a hypothesis.
- Every loop exit has a recorded reason, which makes eval failures diagnosable.

### Drawbacks & Limitations
- Up to `MAX_RETRIEVAL_ROUNDS` LLM calls per investigation instead of 1 (higher cost and latency; more exposure to provider timeouts).
- Convergence detection compares titles, confidence and citation sets; a reworded but equivalent hypothesis is treated as a change and may use one extra round.

### Trade-offs & Mitigations
- Cost is bounded by the round cap and by skipping LLM calls when a round finds nothing new.
- Prior benchmark results (one LLM call per case) are not comparable with runs after this change.

---

## 6. Implementation References

- **Graph**: [`src/issuelens/agent/graph.py`](../../src/issuelens/agent/graph.py)
- **Nodes**: `reevaluate_hypotheses()`, `evaluate_evidence()`, `retrieve_followup()`, `decide_next_step()` in [`src/issuelens/agent/nodes.py`](../../src/issuelens/agent/nodes.py)
- **Prompt**: `build_reevaluation_messages()` in [`src/issuelens/agent/prompts.py`](../../src/issuelens/agent/prompts.py)
- **State**: `consumed_queries`, `new_evidence_ids`, `llm_calls`, `stop_reason` in [`src/issuelens/agent/state.py`](../../src/issuelens/agent/state.py)
- **Tests**: re-evaluation tests in [`tests/unit/test_graph.py`](../../tests/unit/test_graph.py)

---

## 7. Conditions That Would Justify Revisiting the Decision

Revisit this decision if:
- A re-run benchmark shows re-evaluation does not improve hypothesis-file hit rate versus single-pass, while increasing cost.
- Provider latency makes multi-call investigations exceed acceptable API response times (consider moving execution to a background worker).

---

## 8. Related ADRs

- [ADR-005: LangGraph Agent Workflow](ADR-005-langgraph-agent-workflow-and-state-machine.md): Amended by this ADR.
- [ADR-003: Deterministic Citation Validation](ADR-003-deterministic-citation-validation-and-insufficient-evidence.md): Validator now computes `support_status`.
