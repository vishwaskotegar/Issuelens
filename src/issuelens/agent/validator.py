"""Deterministic evidence citation validator and insufficient-evidence guardrails."""

import re

from pydantic import BaseModel, ConfigDict, Field

from issuelens.agent.report import (
    EvidenceItem,
    Hypothesis,
    InvestigationReport,
    VerificationStatus,
)
from issuelens.core.errors import (
    ResourceNotFoundError,
    SafetyViolationError,
)
from issuelens.core.logging import get_logger
from issuelens.repository.snapshot import RepoSnapshot
from issuelens.repository.snippets import read_snippet

logger = get_logger("issuelens.agent.validator")


class CitationValidationResult(BaseModel):
    """Detailed verification outcome for an individual EvidenceItem."""

    model_config = ConfigDict(extra="ignore")

    evidence_id: str = Field(..., description="Target evidence item identifier")
    is_valid: bool = Field(..., description="Whether evidence item is completely verified")
    source_type: str = Field(..., description="Evidence source type (code, doc, issue, commit)")
    path_or_url: str = Field(..., description="Referenced path or URL")
    file_exists: bool = Field(
        default=False, description="Whether target file exists in snapshot"
    )
    lines_valid: bool = Field(
        default=False, description="Whether start and end lines are in bounds"
    )
    commit_matches: bool = Field(
        default=False, description="Whether commit SHA matches snapshot"
    )
    excerpt_matches: bool = Field(
        default=False, description="Whether excerpt text matches snapshot"
    )
    error_reason: str | None = Field(default=None, description="Explanation if validation failed")


class ReportValidationResult(BaseModel):
    """Aggregate validation outcome for an InvestigationReport."""

    model_config = ConfigDict(extra="ignore")

    is_valid: bool = Field(..., description="Overall validity of the report's citations")
    verification_status: VerificationStatus = Field(
        ..., description="Computed verification status (verified | partially_verified | not_run)"
    )
    citation_results: list[CitationValidationResult] = Field(
        default_factory=list, description="Validation result for each evidence item"
    )
    valid_evidence_ids: list[str] = Field(
        default_factory=list, description="List of evidence IDs verified against snapshot"
    )
    invalid_evidence_ids: list[str] = Field(
        default_factory=list, description="List of evidence IDs that failed verification"
    )
    ungrounded_hypotheses: list[str] = Field(
        default_factory=list,
        description="Hypotheses citing invalid or nonexistent evidence IDs",
    )
    insufficient_evidence: bool = Field(
        default=False, description="Whether evidence is deemed insufficient to diagnose root cause"
    )
    insufficient_evidence_reasons: list[str] = Field(
        default_factory=list, description="Specific reasons why evidence was deemed insufficient"
    )


def normalize_line_text(line: str) -> str:
    """Normalize line by stripping line number prefixes (e.g. '10: ') and trailing whitespace."""
    cleaned = re.sub(r"^\s*\d+:\s*", "", line)
    return cleaned.rstrip()


def excerpts_match(expected_snippet: str, candidate_excerpt: str) -> bool:
    """Compare candidate excerpt against physical snippet using normalized line comparison."""
    expected_lines = [
        normalize_line_text(ln)
        for ln in expected_snippet.splitlines()
        if ln.strip()
    ]
    candidate_lines = [
        normalize_line_text(ln)
        for ln in candidate_excerpt.splitlines()
        if ln.strip()
    ]

    if not expected_lines or not candidate_lines:
        return False

    if expected_lines == candidate_lines:
        return True

    # Allow candidate to be a contiguous subset of the expected lines
    expected_text = "\n".join(expected_lines)
    candidate_text = "\n".join(candidate_lines)
    return candidate_text in expected_text or expected_text in candidate_text


def validate_citation(
    evidence: EvidenceItem,
    snapshot: RepoSnapshot,
) -> CitationValidationResult:
    """Validate a single EvidenceItem against the physical repository snapshot."""
    if evidence.source_type != "code":
        # Non-code sources (issues, commits, docs) validated by format checks
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=True,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            file_exists=True,
            lines_valid=True,
            commit_matches=True,
            excerpt_matches=True,
        )

    # 1. Commit SHA verification
    commit_matches = True
    if evidence.commit_sha and evidence.commit_sha != snapshot.commit_sha:
        commit_matches = False
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=False,
            error_reason=(
                f"Commit SHA mismatch: cited '{evidence.commit_sha}', "
                f"snapshot is pinned to '{snapshot.commit_sha}'."
            ),
        )

    # 2. Path containment and existence check
    try:
        resolved_file = snapshot.safe_resolve(evidence.path_or_url)
    except SafetyViolationError as exc:
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=commit_matches,
            error_reason=f"Path safety violation: {exc}",
        )
    except ResourceNotFoundError:
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=commit_matches,
            file_exists=False,
            error_reason=f"File '{evidence.path_or_url}' does not exist in snapshot.",
        )

    # 3. Line range checks
    if evidence.start_line is None or evidence.end_line is None:
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=commit_matches,
            file_exists=True,
            lines_valid=False,
            error_reason="Code citations must specify both start_line and end_line.",
        )

    if evidence.start_line < 1 or evidence.end_line < evidence.start_line:
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=commit_matches,
            file_exists=True,
            lines_valid=False,
            error_reason=f"Invalid line coordinates: {evidence.start_line}-{evidence.end_line}.",
        )

    try:
        content = resolved_file.read_text(encoding="utf-8", errors="replace")
        total_lines = len(content.splitlines())
    except OSError as exc:
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=commit_matches,
            file_exists=True,
            lines_valid=False,
            error_reason=f"Failed to read file from disk: {exc}",
        )

    if evidence.start_line > total_lines or evidence.end_line > total_lines:
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=commit_matches,
            file_exists=True,
            lines_valid=False,
            error_reason=(
                f"Line range {evidence.start_line}-{evidence.end_line} exceeds "
                f"total lines in file ({total_lines})."
            ),
        )

    # 4. Physical excerpt comparison
    actual_snippet = read_snippet(
        snapshot=snapshot,
        file_path=evidence.path_or_url,
        start_line=evidence.start_line,
        end_line=evidence.end_line,
    )

    matches = excerpts_match(actual_snippet, evidence.excerpt)
    if not matches:
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=commit_matches,
            file_exists=True,
            lines_valid=True,
            excerpt_matches=False,
            error_reason=(
                "Excerpt content does not match physical file lines in repository snapshot."
            ),
        )

    return CitationValidationResult(
        evidence_id=evidence.evidence_id,
        is_valid=True,
        source_type=evidence.source_type,
        path_or_url=evidence.path_or_url,
        commit_matches=commit_matches,
        file_exists=True,
        lines_valid=True,
        excerpt_matches=True,
    )


def validate_investigation_report(
    report: InvestigationReport,
    snapshot: RepoSnapshot,
) -> tuple[InvestigationReport, ReportValidationResult]:
    """Validate report citations against snapshot and enforce insufficient-evidence behaviors.

    Returns:
        tuple[InvestigationReport, ReportValidationResult]:
            Sanitized InvestigationReport and full validation diagnostics.
    """
    citation_results: list[CitationValidationResult] = []
    valid_ids: set[str] = set()
    invalid_ids: set[str] = set()

    # 1. Validate each evidence citation against snapshot
    for item in report.evidence:
        res = validate_citation(item, snapshot)
        citation_results.append(res)
        if res.is_valid:
            valid_ids.add(res.evidence_id)
        else:
            invalid_ids.add(res.evidence_id)
            logger.warning(
                "Invalid citation detected: %s (%s) - %s",
                res.evidence_id,
                res.path_or_url,
                res.error_reason,
            )

    # 2. Validate hypothesis citation links and sanitize ungrounded citations
    sanitized_hypotheses: list[Hypothesis] = []
    ungrounded_titles: list[str] = []

    for hyp in report.hypotheses:
        cited_ids = hyp.supporting_evidence_ids
        verified_supporting_ids = [eid for eid in cited_ids if eid in valid_ids]
        bad_supporting_ids = [eid for eid in cited_ids if eid not in valid_ids]

        # Check contradicting evidence ids as well
        verified_contradicting_ids = [
            eid for eid in hyp.contradicting_evidence_ids if eid in valid_ids
        ]

        confidence = hyp.confidence
        rationale = hyp.confidence_rationale

        if bad_supporting_ids:
            ungrounded_titles.append(hyp.title)
            # Demote confidence if supporting evidence was invalid or hallucinated
            if not verified_supporting_ids:
                confidence = "low"
                demotion_note = (
                    f" [Citation Guardrail: Cited evidence {bad_supporting_ids} failed "
                    "physical validation against snapshot; confidence demoted to low.]"
                )
                rationale = (rationale + demotion_note).strip()
            else:
                demotion_note = (
                    f" [Citation Guardrail: Partial citations {bad_supporting_ids} "
                    "invalid; retained verified citations only.]"
                )
                rationale = (rationale + demotion_note).strip()

        sanitized_hyp = Hypothesis(
            title=hyp.title,
            explanation=hyp.explanation,
            confidence=confidence,
            confidence_rationale=rationale,
            supporting_evidence_ids=verified_supporting_ids,
            contradicting_evidence_ids=verified_contradicting_ids,
            verification_steps=hyp.verification_steps,
        )
        sanitized_hypotheses.append(sanitized_hyp)

    # 3. Evaluate insufficient-evidence status
    insufficient_evidence = False
    insufficient_reasons: list[str] = []

    if len(valid_ids) == 0:
        insufficient_evidence = True
        insufficient_reasons.append(
            "No verified source code evidence was retrieved from the repository snapshot."
        )

    if not sanitized_hypotheses:
        insufficient_evidence = True
        insufficient_reasons.append(
            "No plausible root-cause hypotheses could be formulated from available evidence."
        )
    elif all(h.confidence == "low" for h in sanitized_hypotheses):
        insufficient_evidence = True
        insufficient_reasons.append(
            "All formulated hypotheses carry low confidence due to limited or missing citations."
        )

    # 4. Compute overall verification status
    computed_status: VerificationStatus
    if len(invalid_ids) == 0 and len(valid_ids) > 0 and not ungrounded_titles:
        computed_status = "verified"
    elif len(valid_ids) > 0:
        computed_status = "partially_verified"
    else:
        computed_status = "not_run"

    # 5. Enrich report with missing information and limitations if evidence is weak
    updated_missing_info = list(report.missing_information)
    updated_limitations = list(report.limitations)
    updated_summary = report.summary

    if insufficient_evidence:
        for reason in insufficient_reasons:
            if reason not in updated_missing_info:
                updated_missing_info.append(f"Insufficient Evidence Warning: {reason}")
        guardrail_limitation = (
            "Citation Guardrail: Findings are inconclusive due to insufficient verified evidence."
        )
        if guardrail_limitation not in updated_limitations:
            updated_limitations.append(guardrail_limitation)

        if not updated_summary.startswith("[INSUFFICIENT EVIDENCE]"):
            updated_summary = f"[INSUFFICIENT EVIDENCE] {updated_summary}".strip()

    # Filter out invalid evidence items from final verified report so invalid
    # citations cannot silently appear.
    verified_evidence_items = [
        item for item in report.evidence if item.evidence_id in valid_ids
    ]

    sanitized_report = InvestigationReport(
        run_id=report.run_id,
        issue_url=report.issue_url,
        repository=report.repository,
        commit_sha=report.commit_sha,
        summary=updated_summary,
        observed_facts=report.observed_facts,
        hypotheses=sanitized_hypotheses,
        evidence=verified_evidence_items,
        missing_information=updated_missing_info,
        suggested_tests=report.suggested_tests,
        recommended_next_steps=report.recommended_next_steps,
        verification_status=computed_status,
        limitations=updated_limitations,
    )

    validation_result = ReportValidationResult(
        is_valid=len(invalid_ids) == 0 and not ungrounded_titles,
        verification_status=computed_status,
        citation_results=citation_results,
        valid_evidence_ids=sorted(valid_ids),
        invalid_evidence_ids=sorted(invalid_ids),
        ungrounded_hypotheses=ungrounded_titles,
        insufficient_evidence=insufficient_evidence,
        insufficient_evidence_reasons=insufficient_reasons,
    )

    return sanitized_report, validation_result
