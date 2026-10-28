# ADR-XXX: [Short, Descriptive Title of Decision]

- **Status**: [Draft | Accepted | Superseded | Deprecated | Rejected]
- **Date**: YYYY-MM-DD
- **Deciders**: [List of contributors or roles involved]
- **Phase**: [Phase N: Phase Name]
- **Nature of Rationale**: [Original | Reconstructed] *(Note: If documenting an existing decision whose original rationale was not recorded at the time, label as Reconstructed. Do not fabricate historical discussions.)*

---

## 1. Context and Problem Statement
*What problem are we solving? What are the architectural, technical, operational, or business drivers? What constraints (security, performance, budget, safety boundaries) must be respected?*

[Describe the context, requirements, and challenges that led to this decision. Include relevant issue numbers, user stories, or specification sections.]

---

## 2. Decision Made
*What is the change, architecture, pattern, or choice that we are committing to?*

[State the chosen decision clearly and unambiguously. Reference the affected components, file paths, protocols, or schemas where applicable.]

---

## 3. Alternatives Considered
*What other approaches or designs were evaluated before making this decision?*

### Alternative A: [Name of Alternative]
- **Description**: [Brief summary of the alternative approach]
- **Pros**: [What advantages did it offer?]
- **Cons**: [Why was it ultimately not chosen?]

### Alternative B: [Name of Alternative]
- **Description**: [Brief summary of the alternative approach]
- **Pros**: [What advantages did it offer?]
- **Cons**: [Why was it ultimately not chosen?]

---

## 4. Reasons for Choosing the Selected Approach
*Why does the chosen approach win over the alternatives?*

1. **[Key Reason 1]**: [Explanation in the context of IssueLens principles (e.g., safety, determinism, testability, simplicity)]
2. **[Key Reason 2]**: [Explanation]
3. **[Key Reason 3]**: [Explanation]

---

## 5. Benefits, Drawbacks, and Trade-offs
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

## 6. Consequences for Future Development
*How does this decision affect subsequent phases, future code, tests, and team workflows?*

- **On Codebase & Modules**: [Impact on existing/future packages]
- **On Testing**: [How testing strategies or mocking are influenced]
- **On Dependencies**: [New packages or infrastructure requirements]
- **On Security & Safety Boundaries**: [Alignment with read-only, non-execution rules]

---

## 7. Conditions for Reconsideration
*Under what specific conditions, metrics, scale thresholds, or changed assumptions should this decision be reopened or superseded?*

Reconsider this decision if:
- [Condition 1: e.g., Request volume exceeds X, or latency requirements drop below Y ms]
- [Condition 2: e.g., GitHub API introduces breaking deprecations or new preferred endpoints]
- [Condition 3: e.g., Evaluation benchmark demonstrates that an alternative significantly outperforms this approach]

