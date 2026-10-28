# Architecture Decision Records (ADRs)

This directory contains the **Architecture Decision Records (ADRs)** for the IssueLens project.

An Architecture Decision Record captures an important architectural, design, or technical decision along with its context, alternatives considered, rationale, trade-offs, and consequences. ADRs serve as the persistent institutional memory of *why* IssueLens is structured the way it is.

---

## When to Write an ADR

Write an ADR for **significant architectural, infrastructure, or technical design decisions**. 

### Write an ADR when:
- Introducing a major architectural pattern (e.g., AST-based symbol parsing vs. language servers, protocol-based client injection vs. direct instantiation).
- Choosing or swapping an external framework, database, or orchestration library (e.g., LangGraph for agent orchestration, PostgreSQL with pgvector, Alembic for migrations).
- Designing core data schemas or contract boundaries (e.g., report schema format, citation provenance guarantees).
- Setting critical security, sandboxing, or safety boundaries (e.g., read-only repository inspection policy, SSRF defenses on issue URLs).
- Making a trade-off that significantly impacts developer ergonomics, performance, testability, or future maintainability.

### Do NOT write an ADR for:
- Minor implementation details (e.g., naming a variable, helper function splits).
- Routine bug fixes or tactical patches (use [`docs/BUILD_LOG.md`](file:///c:/Projects/Issuelens/docs/BUILD_LOG.md) for root-cause analyses and debugging lessons).
- Standard linting rules or code formatting adjustments.
- Routine dependency version bumps that do not change system behavior.

---

## How to Write an ADR

1. **Copy the Template**: Copy [`docs/decisions/ADR-TEMPLATE.md`](file:///c:/Projects/Issuelens/docs/decisions/ADR-TEMPLATE.md) to a new file in this directory.
2. **Assign the Next Sequential Number**: Follow the naming convention `ADR-XXX-<kebab-case-title>.md` using three digits (e.g., `ADR-001-github-api-client.md`, `ADR-002-deterministic-code-search.md`).
3. **Fill Out All Sections**:
   - **Title and Status**: `Draft`, `Accepted`, `Superseded`, `Deprecated`, or `Rejected`.
   - **Nature of Rationale**: Explicitly record whether the rationale was captured during design (**Original**) or documented retroactively (**Reconstructed**).
   - **Context and Problem**: What driven requirements or constraints prompted this decision?
   - **Decision Made**: What was chosen?
   - **Alternatives Considered**: What viable alternatives were evaluated and why were they passed over?
   - **Reasons for Choosing**: Why this option best aligns with IssueLens principles (safety, testability, determinism).
   - **Benefits, Drawbacks, and Trade-offs**: Objective accounting of gains and sacrifices.
   - **Consequences for Future Development**: Impact on upcoming phases, tests, and interfaces.
   - **Conditions for Reconsideration**: Clear triggers for when this decision should be re-evaluated.
4. **Link to Code & Tests**: Reference actual repository paths (`src/issuelens/...`, `tests/...`) where the decision is realized.
5. **Update the Index**: Add the new ADR to the [ADR Index](#adr-index) below.

---

## Integrity & Historical Rationale Policy

- **No Fabricated History**: Never invent a fictional historical decision-making narrative, meeting, or consensus. 
- **Reconstructed Records**: If documenting an architectural decision that was already implemented in an earlier milestone without a contemporaneous record, explicitly set:
  ```markdown
  - **Nature of Rationale**: Reconstructed
  ```
  Clearly state that the rationale is reconstructed from the existing codebase and build specifications.
- **Living Status**: When a decision is changed or replaced by a newer decision, do not delete or edit the old record; instead, mark the old record's status as `Superseded by ADR-YYY` and link to the new record.

---

## ADR Index

| ADR | Title | Status | Date | Phase | Rationale Type |
| :--- | :--- | :--- | :--- | :--- | :--- |
| [ADR-001](file:///c:/Projects/Issuelens/docs/decisions/ADR-001-github-api-client.md) | GitHub REST API Client Adapter with Dependency Injection Protocol | Accepted | 2026-10-26 | Phase 1 | Reconstructed |

---

## Relationship to Other Documentation

- **`docs/BUILD_SPECIFICATION.md`**: The master product and engineering specification defining features, phases, and acceptance criteria.
- **`docs/LEARNING_GUIDE.md`**: Pedagogical guide explaining how each feature works step-by-step from senior to junior engineer.
- **`docs/BUILD_LOG.md`**: Tactical engineering journal recording bugs, failures, verified root causes, verification steps, and lessons learned during implementation.
- **`docs/decisions/` (ADRs)**: Strategic architectural decisions, alternatives evaluated, trade-offs, and conditions for reconsideration.

