# Architecture Decision Records (ADRs)

This directory contains the **Architecture Decision Records (ADRs)** for the IssueLens project.

An Architecture Decision Record captures an important architectural, design, or technical decision along with its context, alternatives considered, rationale, trade-offs, and consequences. ADRs serve as the persistent institutional memory of *why* IssueLens is structured the way it is.

---

## 1. What is an ADR and Why We Use Them

As a software system evolves, engineers make critical decisions about patterns, libraries, safety boundaries, data models, and protocols. Months later, the original context is often lost, leading to:
- Accidental regressions where discarded bad patterns are reintroduced.
- Fear of changing architecture because "nobody remembers why it was done this way."
- Time wasted debating already-settled decisions without new evidence.

ADRs solve this by recording decisions at the moment they are made in a concise, structured format.

---

## 2. When to Create an ADR

Create an ADR when a decision **materially affects**:
- **Architecture**: Core patterns (e.g., AST-based symbol parsing vs. language servers, protocol-based client injection vs. direct instantiation).
- **Maintainability & Extensibility**: Package structures, decoupling abstractions, or public interfaces.
- **Performance & Cost**: Snapshot caching strategies, indexing algorithms, or token/context budgeting.
- **Security & Safety Boundaries**: Read-only repository inspection policy, SSRF defenses on issue URLs, prompt injection isolation.
- **Data Modeling & Contracts**: Report schema format, evidence citation provenance guarantees, relational persistence models.
- **Third-Party Frameworks & Services**: Choosing or swapping an external framework, database, or orchestration library (e.g., LangGraph for agent orchestration, PostgreSQL with pgvector, Alembic for migrations).

### Do NOT create an ADR for:
- Trivial implementation choices (e.g., naming a variable, helper function splits).
- Routine bug fixes or tactical patches (use [`docs/BUILD_LOG.md`](file:///c:/Projects/Issuelens/docs/BUILD_LOG.md) for root-cause analyses and debugging lessons).
- Standard linting rules or code formatting adjustments.
- Routine dependency version bumps that do not change system behavior.

---

## 3. How to Create and Maintain an ADR

1. **Copy the Template**: Copy [`docs/decisions/ADR-TEMPLATE.md`](file:///c:/Projects/Issuelens/docs/decisions/ADR-TEMPLATE.md) to a new file in this directory.
2. **Assign the Next Sequential Number**: Follow the naming convention `ADR-XXX-<kebab-case-title>.md` using three digits, for example:
   - `ADR-001-github-api-client.md`
   - `ADR-002-code-search-strategy.md`
   *(These are examples of naming conventions, not decisions that should automatically be created.)*
3. **Fill Out All Required Sections**:
   - ADR number and title
   - Status: `Proposed`, `Accepted`, `Superseded`, or `Rejected`
   - Date
   - Context and problem
   - Decision and rationale
   - Alternatives considered
   - Why the alternatives were not selected
   - Trade-offs and consequences
   - Implementation references
   - Conditions that would justify revisiting the decision
   - Related ADRs
4. **Link to Code & Tests**: Reference actual repository paths (`src/issuelens/...`, `tests/...`) where the decision is realized.
5. **Update the Index**: Add the new ADR to the [ADR Index](#4-adr-index) below.
6. **Maintain Lifecycle & Historical Integrity**: When an existing decision changes, update its status (e.g., `Superseded by ADR-YYY`) and cross-reference the replacement ADR rather than silently rewriting history.

---

## 4. Integrity & Historical Rationale Policy

- **No Fabricated History**: Never invent a fictional historical decision-making narrative, meeting, or consensus. 
- **Reconstructed Records**: If documenting an architectural decision that was already implemented in an earlier milestone without a contemporaneous record, explicitly set:
  ```markdown
  - **Nature of Rationale**: Reconstructed
  ```
  Clearly state that the rationale is reconstructed from the existing codebase and build specifications.
- **Living Status**: When a decision is changed or replaced by a newer decision, do not delete or edit the old record; instead, mark the old record's status as `Superseded by ADR-YYY` and link to the new record.

---

## 5. ADR Index

| ADR | Title | Status | Date | Phase | Rationale Type |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [**ADR-001**](file:///c:/Projects/Issuelens/docs/decisions/ADR-001-github-api-client.md) | GitHub REST API Client Adapter with Dependency Injection Protocol | Accepted | 2026-10-26 | Phase 1 | Reconstructed |
| [**ADR-002**](file:///c:/Projects/Issuelens/docs/decisions/ADR-002-llm-provider-adapter-and-report-generation.md) | LangChain Provider Adapter with Structured Output and Snapshot Provenance Binding | Accepted | 2026-10-29 | Phase 3 | Original |
| [**ADR-003**](file:///c:/Projects/Issuelens/docs/decisions/ADR-003-deterministic-citation-validation-and-insufficient-evidence.md) | Deterministic Evidence Citation Validation and Insufficient-Evidence Guardrails | Accepted | 2026-10-29 | Phase 4 | Original |
| [**ADR-004**](file:///c:/Projects/Issuelens/docs/decisions/ADR-004-commit-pinned-snapshot-cache-and-disk-bounds.md) | Commit-Pinned Repository Snapshot Disk Cache with Bounded LRU Eviction | Accepted | 2026-10-29 | Phase 4 | Original |

---

## 6. Relationship to Other Documentation

```text
docs/
├── BUILD_SPECIFICATION.md   # Master product and engineering build specification (Source of Truth)
├── architecture/            # High-level and detailed architecture blueprints & flows
│   ├── README.md
│   ├── system-overview.md
│   ├── request-lifecycle.md
│   ├── agent-workflow.md
│   ├── codebase-map.md
│   ├── data-flow-and-trust-boundaries.md
│   └── testing-and-evaluation.md
├── LEARNING_GUIDE.md        # Senior-to-junior pedagogical guide (What, How, Concepts, Trade-offs)
├── decisions/ (ADRs)        # Strategic architectural decisions, alternatives, and trade-offs
└── BUILD_LOG.md             # Tactical engineering log (Bugs, Root Causes, Fixes, Lessons)
```
