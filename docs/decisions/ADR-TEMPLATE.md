# ADR-XXX: [Short, Descriptive Title of Decision]

- **Status**: [Proposed | Accepted | Superseded | Rejected]
- **Date**: YYYY-MM-DD
- **Deciders**: [List of contributors or roles involved]
- **Phase**: [Phase N: Phase Name]
- **Nature of Rationale**: [Original | Reconstructed] *(Note: If documenting an existing decision whose original rationale was not recorded at the time, label as Reconstructed. Do not fabricate historical discussions.)*

---

## 1. Context and Problem
*What problem are we solving? What are the architectural, technical, operational, or business drivers? What constraints (security, performance, budget, safety boundaries) must be respected?*

[Describe the context, requirements, and challenges that led to this decision. Include relevant issue numbers, user stories, or specification sections.]

---

## 2. Decision and Rationale
*What is the change, architecture, pattern, or choice that we are committing to, and what is the underlying rationale?*

[State the chosen decision clearly and unambiguously. Reference the affected components, file paths, protocols, or schemas where applicable, and explain the core reasons why this choice best solves the problem.]

---

## 3. Alternatives Considered
*What other viable approaches or designs were evaluated before making this decision?*

### Alternative A: [Name of Alternative]
- **Description**: [Brief summary of the alternative approach]
- **Pros**: [What advantages did it offer?]
- **Cons**: [Why was it ultimately not chosen?]

### Alternative B: [Name of Alternative]
- **Description**: [Brief summary of the alternative approach]
- **Pros**: [What advantages did it offer?]
- **Cons**: [Why was it ultimately not chosen?]

---

## 4. Why the Alternatives Were Not Selected
*Why were the evaluated alternatives passed over in favor of the chosen approach?*

1. **[Reason 1 regarding Alternative A]**: [Detailed explanation why it falls short of IssueLens safety, testability, or maintainability principles]
2. **[Reason 2 regarding Alternative B]**: [Detailed explanation why it falls short]

---

## 5. Trade-offs and Consequences
*Every architectural choice involves trade-offs. Be honest about what we gain and what we compromise.*

### Benefits (Positive Consequences)
- [Benefit 1]
- [Benefit 2]

### Drawbacks & Limitations (Negative Consequences)
- [Drawback 1]
- [Drawback 2]

### Trade-offs & Mitigations
- [Trade-off 1 and how we mitigate it]
- [Trade-off 2 and how we mitigate it]

---

## 6. Implementation References
*Which source files, classes, functions, models, configuration settings, or tests realize this decision?*

- **Primary Implementation**: [`src/issuelens/...`](file:///c:/Projects/Issuelens/src/issuelens/...)
- **Configuration Settings**: [`src/issuelens/config.py`](file:///c:/Projects/Issuelens/src/issuelens/config.py)
- **Unit & Integration Tests**: [`tests/unit/...`](file:///c:/Projects/Issuelens/tests/unit/...)

---

## 7. Conditions That Would Justify Revisiting the Decision
*Under what specific conditions, metrics, scale thresholds, or changed assumptions should this decision be reopened or superseded?*

Revisit this decision if:
- [Condition 1: e.g., Request volume exceeds X, or latency requirements drop below Y ms]
- [Condition 2: e.g., GitHub API introduces breaking deprecations or new preferred endpoints]
- [Condition 3: e.g., Evaluation benchmark demonstrates that an alternative significantly outperforms this approach]

---

## 8. Related ADRs
*Which other Architecture Decision Records relate to, depend upon, or are affected by this decision?*

- [ADR-001: GitHub REST API Client Adapter](file:///c:/Projects/Issuelens/docs/decisions/ADR-001-github-api-client.md): [Example relationship: Precedes / Complements / Supersedes]
- None (if standalone)
