# ADR-005: LangGraph Agent Workflow and Cyclic State Machine

- **Status**: Accepted
- **Date**: 2026-11-04
- **Deciders**: Senior Engineering Team, Coding Agent
- **Phase**: Phase 5 (LangGraph Workflow)
- **Nature of Rationale**: Original

---

## 1. Context and Problem
In Phases 3 and 4, IssueLens used a linear synchronous pipeline within `InvestigationService.investigate()`. While this established baseline end-to-end report generation and physical citation validation, real-world bug investigation is inherently non-linear and iterative:
1. **Uncertainty & Incomplete Initial Evidence**: The initial query often identifies high-level entrypoints or stack frame locations, but lacks crucial secondary evidence such as caller functions, callee implementations, or internal configuration constants.
2. **Missing Falsification Loop**: A single-pass pipeline generates hypotheses and immediately builds a report without testing or attempting to refute unproven claims.
3. **Execution Guardrails & Bounded Termination**: An iterative agent workflow must never enter unbounded recursion or infinite loops that deplete LLM tokens, exceed execution timeouts, or trigger excessive disk I/O.
4. **Auditability & Observability**: Every tool execution, retrieval attempt, duration, and intermediate error must be captured in an immutable trace for reproduction and debugging (US-07).

---

## 2. Decision and Rationale
We adopt **LangGraph** (`langgraph>=0.2.0`) to structure the investigation process as an explicit, typed, cyclic state machine with deterministic conditional routing and hard execution limits:

1. **Serializable Typed State (`InvestigationState`)**: Defined in [`src/issuelens/agent/state.py`](../../src/issuelens/agent/state.py) as a Python `TypedDict`, capturing issue context, extracted symptoms, accumulated evidence, ranked hypotheses, missing information, retrieval round count, tool traces, and final report. Large physical snapshot objects are kept out of state and injected via dependency nodes.
2. **Discrete Functional Nodes (`InvestigationNodes`)**: Defined in [`src/issuelens/agent/nodes.py`](../../src/issuelens/agent/nodes.py):
   - `parse_issue`: Deterministic regex extraction of error types, python files, symbols, and stack frames.
   - `retrieve_initial`: Exact lexical and AST symbol search (max 10 hits).
   - `form_hypotheses`: Structured LLM hypothesis generation with XML prompt injection enclaves.
   - `evaluate_evidence`: Grounding assessment, hypothesis support status tagging, and targeted query formulation.
   - `retrieve_followup`: Bounded secondary search for missing evidence items.
   - `validate_citations`: Physical disk byte validation of line numbers and excerpts against snapshot.
   - `build_report`: Synthesis of final Pydantic `InvestigationReport` with insufficient-evidence fallbacks.
3. **Conditional Router with Hard Stop Conditions (`decide_next_step`)**:
   - Loops back from `evaluate_evidence` to `retrieve_followup` only if unaddressed queries exist AND `retrieval_round < max_retrieval_rounds` (default: 3).
   - Routes to `validate_citations` immediately upon reaching the loop cap, when queries are exhausted, or when evidence is deemed sufficient.
4. **Resilient Tool Tracing (`ToolTraceItem`)**:
   - Captures tool name, round number, input parameters, duration in milliseconds, status (`success` / `error` / `skipped`), and error messages.
   - Unexpected tool exceptions (e.g. search engine failures) are trapped, recorded in `tool_trace` and `errors`, allowing graceful degradation rather than unhandled workflow crashes.

---

## 3. Alternatives Considered

### Alternative A: Custom While-Loop in Pure Python
- **Description**: Implement a manual `while current_round < max_rounds:` loop inside `InvestigationService`.
- **Pros**: Zero external dependencies added to `pyproject.toml`.
- **Cons**: Conflates control flow with tool logic; lacks standardized state checkpointing, visualization, and future persistence integration; difficult to inspect intermediate step graphs.

### Alternative B: Autonomous Tool-Calling / ReAct Agent (LangChain AgentExecutor)
- **Description**: Give an LLM an open set of search tools and let it autonomously decide which tool to call in a loop.
- **Pros**: Highly flexible and dynamic.
- **Cons**: Nondeterministic execution; vulnerable to prompt injection hijacking; prone to hallucinating tool parameters; difficult to guarantee bounded execution or prove stop conditions.

---

## 4. Why the Alternatives Were Not Selected

1. **Why not Custom While-Loop (Alternative A)**: A bespoke while loop becomes brittle as multi-step branching, error recovery, and persistence are added. LangGraph provides an explicit directed cyclic graph abstraction where state transitions, conditional edges, and node boundaries are strictly formalized and easily tested.
2. **Why not Autonomous ReAct Agent (Alternative B)**: The core product safety boundary requires read-only, deterministic repository inspection. Allowing an LLM to freely control execution loops violates our safety invariants, risks infinite loops, and increases token costs without improving citation precision.

---

## 5. Trade-offs and Consequences

### Benefits (Positive Consequences)
- **Predictable Control Flow**: The graph structure is fully deterministic; all cyclic transitions pass through the audited `decide_next_step` router.
- **Loop Cap Guarantee**: Hard maximum of 3 retrieval rounds prevents runaway LLM costs.
- **Complete Auditability**: `tool_trace` records every action taken by the workflow, enabling precise reproduction of investigations.
- **Fault Tolerance**: Tool failures in code retrieval are trapped and recorded in state without crashing the pipeline.

### Drawbacks & Limitations (Negative Consequences)
- **Additional Dependency**: Adds `langgraph` and its underlying runtime dependencies to the project.
- **State Serialization Overhead**: Requires converting between Pydantic domain models and state dictionaries at graph boundaries.

### Trade-offs & Mitigations
- **Mitigation for Dependency Overhead**: LangGraph was pinned cleanly in `pyproject.toml` (`langgraph>=0.2.0`), tested hermetically with offline mocks, and adds negligible latency (< 10ms execution overhead per node).

---

## 6. Implementation References

- **State Schema**: [`src/issuelens/agent/state.py`](../../src/issuelens/agent/state.py)
- **Workflow Nodes & Router**: [`src/issuelens/agent/nodes.py`](../../src/issuelens/agent/nodes.py)
- **Graph Assembly & Compilation**: [`src/issuelens/agent/graph.py`](../../src/issuelens/agent/graph.py)
- **Service Integration**: [`src/issuelens/agent/service.py`](../../src/issuelens/agent/service.py)
- **Unit & Integration Tests**: [`tests/unit/test_graph.py`](../../tests/unit/test_graph.py)
- **Architecture Specification**: [`docs/architecture/agent-workflow.md`](../architecture/agent-workflow.md)

---

## 7. Conditions That Would Justify Revisiting the Decision

Revisit this decision if:
- Asynchronous task processing (e.g. Celery or ARQ) requires distributed task queues rather than in-process graph execution.
- LangGraph introduces breaking architectural shifts that compromise typed state schemas.
- Evaluation benchmarks (Phase 7) prove that single-pass or alternative retrieval strategies yield equivalent citation recall at lower latency.

---

## 8. Related ADRs

- [ADR-002: LLM Provider Adapter and Structured Report Generation](ADR-002-llm-provider-adapter-and-report-generation.md): Precedes graph hypothesis generation.
- [ADR-003: Deterministic Citation Validation and Insufficient-Evidence Guardrails](ADR-003-deterministic-citation-validation-and-insufficient-evidence.md): Precedes `validate_citations` node.
- [ADR-004: Commit-Pinned Repository Snapshot Disk Cache and Storage Bounds](ADR-004-commit-pinned-snapshot-cache-and-disk-bounds.md): Supplies physical snapshots to graph nodes.

