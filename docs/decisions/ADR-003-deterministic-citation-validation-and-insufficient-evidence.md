# ADR-003: Deterministic Evidence Citation Validation and Insufficient-Evidence Guardrails

- **Status**: Accepted
- **Date**: 2026-10-29
- **Deciders**: Senior Engineering Lead (IssueLens Agent)
- **Phase**: Phase 4: Evidence Validation & Citation Guardrails
- **Nature of Rationale**: Original

---

## 1. Context and Problem Statement

A core safety and integrity requirement of IssueLens is that **no code citation or investigative finding may be fabricated**.

Large language models are inherently prone to hallucinations:
1. Hallucinating files that do not exist in the repository.
2. Citing line numbers outside file boundaries or containing entirely unrelated code.
3. Citing a commit SHA different from the pinned snapshot.
4. Fabricating plausible-sounding code snippets.
5. Inverting or fabricating causality when insufficient information exists, presenting wild guesses with "high" confidence.

If an AI debugging assistant provides code citations that do not exist or point to incorrect lines, developer trust is immediately destroyed and engineering teams waste time chasing phantom bugs.

Furthermore, naive agent architectures either accept LLM citations uncritically or rely on another LLM prompt ("Are you sure this citation is real?") which frequently exhibits self-confirming bias.

We needed a deterministic, verifiable, offline guardrail subsystem that guarantees that:
- Every code citation is physically verified against the immutable snapshot on disk at the exact pinned commit SHA.
- Invalid citations can never silently appear in the final report.
- Hypotheses citing invalid or nonexistent evidence are automatically demoted and annotated.
- Reports lacking verified evidence prominently trigger an insufficient-evidence fallback.

---

## 2. Decision Made

We implemented a deterministic post-generation validation and sanitation pipeline in [`src/issuelens/agent/validator.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/validator.py) with the following architectural rules:

1. **Deterministic Multi-Stage Citation Validation (`validate_citation`)**:
   Every `EvidenceItem` with `source_type="code"` is checked against the physical `RepoSnapshot`:
   - **Path Containment & Safety**: Relative paths are checked against directory traversal (`../`) and forbidden root escapes using `validate_safe_relative_path`.
   - **File Existence**: The referenced file must exist in `snapshot.list_files()`.
   - **Commit SHA Integrity**: `evidence.commit_sha` must match `snapshot.commit_sha`.
   - **1-Indexed Line Span Bounds**: `1 <= start_line <= end_line <= total_lines_in_file`.
   - **Physical Excerpt Text Matching**: The snippet is read from disk using `read_snippet(snapshot, file_path, start_line, end_line)`. The lines are normalized (stripping line-number prefixes and trimming whitespace) and compared line-by-line against the claimed excerpt.

2. **Silently Fabricated Evidence Elimination**:
   Any citation failing validation is rejected. Invalid items are pruned from `report.evidence`, ensuring only physically verified evidence items are present in the final output.

3. **Hypothesis Evidence Sanitation & Demotion**:
   For each hypothesis in the report:
   - Any citation ID not in the verified evidence set is purged from `supporting_evidence_ids`.
   - If a hypothesis lost *all* supporting evidence items, its confidence is unconditionally demoted to `"low"`, and an explicit warning note is appended to `confidence_rationale`:
     `"[Citation Guardrail: Cited evidence items were invalid or ungrounded against the repository snapshot; confidence demoted to low.]"`
   - If a hypothesis lost *some* citations but retains at least one valid citation, an informational note is appended to `confidence_rationale` detailing which citations were removed.

4. **Formal Separation of Observed Facts vs. Inferences**:
   In accordance with the product contract, `observed_facts` (immutable statements directly extracted from issue title, body, error messages, and repository files) are kept separate from `hypotheses` (probabilistic causal inferences).

5. **Insufficient-Evidence Fallback Behavior**:
   When zero verified code citations exist, or the LLM could not ground any plausible hypotheses:
   - The report summary is prepended with `"[INSUFFICIENT EVIDENCE]"`.
   - Structured warnings are appended to `report.missing_information` and `report.limitations`.
   - `report.verification_status` is set to `"not_run"` (or `"partially_verified"` if non-code items exist).
   - If all citations are valid, `report.verification_status` is marked `"verified"`.

---

## 3. Alternatives Considered

### Alternative A: LLM Self-Reflection Prompt
- **Description**: Send the generated citations back to the LLM with a prompt asking: *"Did you hallucinate these file paths or line numbers? Please check your work."*
- **Pros**: Easy to implement without file I/O code.
- **Cons**: Completely unreliable. LLMs routinely confirm their own hallucinations with high confidence ("Yes, this file exists at line 42"). It also doubles LLM token latency and cost without offering deterministic guarantees.

### Alternative B: Fail-Fast Exception (Abort Entire Report)
- **Description**: If any citation fails physical verification, raise an HTTP 422 or 502 error and reject the entire investigation.
- **Pros**: Simple all-or-nothing binary outcome.
- **Cons**: Poor user experience. If an LLM accurately identifies the root cause and provides 3 verified citations, but makes a minor error on a 4th reference, aborting the whole run throws away all useful, accurate analysis. Demoting and annotating is significantly more informative and resilient.

### Alternative C: Fuzzy Token Bag Matching
- **Description**: Check if the cited code tokens appear anywhere in the file using Jaccard similarity or fuzzy string matching.
- **Pros**: Tolerant to minor whitespace or formatting variations.
- **Cons**: Violates the precision requirement. In debugging, line numbers and exact functional scope matter immensely. Fuzzy matching risks confirming snippets that actually belong to an entirely different function in the same file.

---

## 4. Reasons for Choosing the Selected Approach

1. **Cryptographic & Physical Truth**: Citations are verified against physical bytes on disk pinned to an immutable git commit SHA. There is zero reliance on LLM self-reporting.
2. **Graceful Degradation with Transparency**: Rather than crashing, the system sanitizes bad citations, demotes ungrounded claims, and explicitly tells the user why a hypothesis was downgraded.
3. **Deterministic & Fast**: Validation requires only local file slicing and string normalization in Python memory. It runs in milliseconds without external network or LLM calls.
4. **Complete Testability**: The entire validation pipeline can be exercised with 100% offline unit tests against test fixtures.

---

## 5. Benefits, Drawbacks, and Trade-offs

### Benefits
- **Zero Hallucinated Citations in Output**: Invalid citations are stripped; users never see phantom file references.
- **Transparent Accountability**: Every hypothesis clearly shows whether its citations survived validation or were pruned.
- **Clear Insufficient Evidence Signaling**: Eliminates confident guessing when data is insufficient.
- **CRLF/LF Tolerance**: Line-by-line whitespace normalization handles Windows (`\r\n`) vs Unix (`\n`) line ending differences seamlessly.

### Drawbacks & Limitations
- **Format Sensitivity**: If an LLM heavily paraphrases code rather than quoting lines, excerpt matching will fail. The system prompt must instruct the model to provide exact snippets.
- **Line Offset Sensitivity**: If the LLM cites lines with a slight offset (e.g. lines 12-15 instead of 10-13), excerpt matching will fail and trigger demotion. (Phase 5 will add iterative search tools allowing the agent to refine line spans before final synthesis).

---

## 6. Consequences for Future Development

- **Phase 5 (LangGraph Agent Loop)**: When the multi-step agent generates candidate citations during intermediate turns, it can invoke `validate_citation` as an internal tool or guardrail before finalizing the report.
- **Phase 6 (Persistence)**: `verification_status` and `citation_results` will be stored in the database schema to allow filtering reports by verification level.
- **Phase 7 (Evaluation Harness)**: Evaluation benchmarks can measure the exact citation validity rate (`valid_citations / total_citations`) as a core metric across model versions.

---

## 7. Conditions for Reconsideration

This decision should be reconsidered if:
- Multi-file semantic diffs or cross-repository citations are introduced where physical local file snapshots are unavailable.
- We implement AST-based AST-node ID citations (e.g., node path `SampleClass.method_name`) alongside or in place of line number spans.

---

## 8. References & Related Documents

- Implementation: [`src/issuelens/agent/validator.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/validator.py)
- Service Integration: [`src/issuelens/agent/service.py`](file:///c:/Projects/Issuelens/src/issuelens/agent/service.py)
- Unit Tests: [`tests/unit/test_validator.py`](file:///c:/Projects/Issuelens/tests/unit/test_validator.py)
- Specification: Section 13 (Phase 4) in [`docs/BUILD_SPECIFICATION.md`](file:///c:/Projects/Issuelens/docs/BUILD_SPECIFICATION.md)

