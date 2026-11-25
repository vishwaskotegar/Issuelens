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
from issuelens.repository.code_search import has_code_substance
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


# Prefix emitted by ``read_snippet`` ("12: code"). Only this exact shape is stripped.
_LINE_NUMBER_PREFIX = re.compile(r"^\d+: ?")
# Heading label prepended to some documentation excerpts ("[Install]\n...").
_HEADING_PREFIX = re.compile(r"^\[[^\]\n]+\]\r?\n")

# Largest cited range the validator will read back in full. Anything wider is not a
# meaningful citation and is rejected rather than silently truncated.
MAX_VALIDATED_RANGE_LINES = 1000

# Evidence types whose content is read from the pinned repository snapshot. Issue and
# commit text is untrusted user-supplied context and never grounds a hypothesis.
REPOSITORY_SOURCE_TYPES = frozenset({"code", "documentation"})


def normalize_line_text(line: str) -> str:
    """Strip a single ``read_snippet`` line-number prefix and trailing whitespace."""
    return _LINE_NUMBER_PREFIX.sub("", line, count=1).rstrip()


def _comparable_lines(text: str, *, strip_prefixes: bool, ignore_indentation: bool) -> list[str]:
    lines: list[str] = []
    for raw in text.splitlines():
        line = _LINE_NUMBER_PREFIX.sub("", raw, count=1) if strip_prefixes else raw
        line = line.strip() if ignore_indentation else line.rstrip()
        if line:
            lines.append(line)
    return lines


def excerpts_match(
    expected_snippet: str,
    candidate_excerpt: str,
    *,
    ignore_indentation: bool = False,
) -> bool:
    """Return True only if the excerpt is exactly the cited lines.

    ``expected_snippet`` is the ``read_snippet`` output for the *whole* cited range.
    The candidate may carry the same ``N: `` prefixes or be raw file text. Trailing
    whitespace and blank lines are ignored; everything else must match line-for-line.
    A fragment, a subset of the range, or text spilling outside the range is rejected:
    any of those would let a citation claim lines it does not show.

    Args:
        expected_snippet: Line-numbered snapshot text for the cited range.
        candidate_excerpt: Excerpt stored on the evidence item.
        ignore_indentation: Also ignore leading whitespace (prose documentation only;
            indentation is semantic in code).
    """
    candidate = _HEADING_PREFIX.sub("", candidate_excerpt, count=1)

    candidate_raw_lines = [ln for ln in candidate.splitlines() if ln.strip()]
    # Strip candidate prefixes only if *every* line carries one, so raw source lines
    # that legitimately start with "200: ..." are left intact.
    candidate_is_prefixed = bool(candidate_raw_lines) and all(
        _LINE_NUMBER_PREFIX.match(ln) for ln in candidate_raw_lines
    )

    expected_lines = _comparable_lines(
        expected_snippet, strip_prefixes=True, ignore_indentation=ignore_indentation
    )
    candidate_lines = _comparable_lines(
        candidate, strip_prefixes=candidate_is_prefixed, ignore_indentation=ignore_indentation
    )

    if not expected_lines or not candidate_lines:
        return False
    return expected_lines == candidate_lines


def validate_citation(
    evidence: EvidenceItem,
    snapshot: RepoSnapshot,
) -> CitationValidationResult:
    """Validate a single EvidenceItem against the physical repository snapshot."""
    if evidence.source_type in ("issue", "commit"):
        # Issue and commit references are validated by provenance checks upstream,
        # not against the repository snapshot filesystem.
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

    # 4. Physical excerpt comparison over the *entire* cited range
    range_length = evidence.end_line - evidence.start_line + 1
    if range_length > MAX_VALIDATED_RANGE_LINES:
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=commit_matches,
            file_exists=True,
            lines_valid=False,
            error_reason=(
                f"Cited range spans {range_length} lines; maximum verifiable range is "
                f"{MAX_VALIDATED_RANGE_LINES}."
            ),
        )

    actual_snippet = read_snippet(
        snapshot=snapshot,
        file_path=evidence.path_or_url,
        start_line=evidence.start_line,
        end_line=evidence.end_line,
        max_snippet_lines=range_length,
    )

    matches = excerpts_match(
        actual_snippet,
        evidence.excerpt,
        ignore_indentation=evidence.source_type == "documentation",
    )
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

    # 5. Substance guardrail for code citations
    if evidence.source_type == "code" and not has_code_substance(evidence.excerpt):
        return CitationValidationResult(
            evidence_id=evidence.evidence_id,
            is_valid=False,
            source_type=evidence.source_type,
            path_or_url=evidence.path_or_url,
            commit_matches=commit_matches,
            file_exists=True,
            lines_valid=True,
            excerpt_matches=True,
            error_reason="Excerpt contains no executable code statements",
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


# Compatibility aliases
validate_evidence_item = validate_citation


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
    # Valid evidence read from the repository snapshot (excludes issue/commit text).
    repository_ids: set[str] = set()

    # Evidence IDs must be unambiguous: hypotheses reference evidence by ID, so if two
    # items share one, a valid item could vouch for an invalid one.
    id_counts: dict[str, int] = {}
    for item in report.evidence:
        id_counts[item.evidence_id] = id_counts.get(item.evidence_id, 0) + 1
    duplicate_ids = {eid for eid, count in id_counts.items() if count > 1}

    # 1. Validate each evidence citation against snapshot
    for item in report.evidence:
        if item.evidence_id in duplicate_ids:
            res = CitationValidationResult(
                evidence_id=item.evidence_id,
                is_valid=False,
                source_type=item.source_type,
                path_or_url=item.path_or_url,
                error_reason="Duplicate evidence_id; citation is ambiguous.",
            )
        else:
            res = validate_citation(item, snapshot)
        citation_results.append(res)
        if res.is_valid:
            valid_ids.add(res.evidence_id)
            if item.source_type in REPOSITORY_SOURCE_TYPES:
                repository_ids.add(res.evidence_id)
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
        repository_support = [eid for eid in verified_supporting_ids if eid in repository_ids]

        if verified_supporting_ids and not repository_support:
            # Only issue/commit text supports this hypothesis. That text is untrusted
            # input (and may simply restate the reporter's guess), so it cannot ground it.
            ungrounded_titles.append(hyp.title)
            confidence = "low"
            rationale = (
                rationale
                + " [Citation Guardrail: Supported only by issue text, not by repository "
                "evidence; confidence demoted to low.]"
            ).strip()
        elif not verified_supporting_ids:
            ungrounded_titles.append(hyp.title)
            confidence = "low"
            if bad_supporting_ids:
                demotion_note = (
                    f" [Citation Guardrail: Cited evidence {bad_supporting_ids} failed "
                    "physical validation against snapshot; confidence demoted to low.]"
                )
            else:
                demotion_note = (
                    " [Citation Guardrail: Hypothesis has no supporting evidence citations; "
                    "confidence demoted to low.]"
                )
            rationale = (rationale + demotion_note).strip()
        elif bad_supporting_ids:
            ungrounded_titles.append(hyp.title)
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
            support_status="supported" if repository_support else "unsupported",
        )
        sanitized_hypotheses.append(sanitized_hyp)

    # 3. Evaluate insufficient-evidence status
    insufficient_evidence = False
    insufficient_reasons: list[str] = []

    if not repository_ids:
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
    grounded_any = any(
        eid in repository_ids for h in sanitized_hypotheses for eid in h.supporting_evidence_ids
    )
    if len(invalid_ids) == 0 and repository_ids and not ungrounded_titles:
        computed_status = "verified"
    elif repository_ids and grounded_any:
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


validate_report_citations = validate_investigation_report
