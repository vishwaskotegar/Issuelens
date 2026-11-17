"""Deterministic evaluation metrics engine for IssueLens benchmark evaluation."""

import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from issuelens.agent.report import InvestigationReport
from issuelens.agent.validator import validate_investigation_report
from issuelens.core.logging import get_logger
from issuelens.evaluation.dataset import EvaluationCaseModel
from issuelens.repository.snapshot import RepoSnapshot

logger = get_logger("issuelens.evaluation.metrics")

_WORD_REGEX = re.compile(r"\b[a-zA-Z_]\w{2,}\b")
_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "when", "where", "which",
    "will", "would", "should", "could", "have", "been", "fixed", "issue", "error",
    "bug", "code", "file", "function", "class", "module", "using", "into", "some",
}


def compute_citation_validity(
    report: InvestigationReport,
    snapshot: RepoSnapshot | None = None,
) -> tuple[int, int, float, list[str]]:
    """Compute citation validity metrics for an InvestigationReport.

    Returns:
        (total_code_citations, valid_code_citations, validity_rate, error_reasons)
    """
    code_items = [
        item for item in report.evidence
        if item.source_type in ("code", "documentation")
    ]
    total_citations = len(code_items)

    if total_citations == 0:
        return 0, 0, 1.0, []

    if snapshot is None:
        # If no snapshot provided, count items with basic non-empty coordinates
        valid = 0
        errors: list[str] = []
        for item in code_items:
            if item.path_or_url and item.start_line is not None and item.end_line is not None:
                valid += 1
            else:
                errors.append(
                    f"Evidence {item.evidence_id} missing path or line range"
                )
        rate = valid / total_citations
        return total_citations, valid, rate, errors

    # Validate against actual repository snapshot
    _, val_result = validate_investigation_report(report, snapshot)
    valid_ids = set(val_result.valid_evidence_ids)
    valid = sum(1 for item in code_items if item.evidence_id in valid_ids)
    rate = valid / total_citations

    errors = [
        f"{cr.evidence_id} ({cr.path_or_url}): {cr.error_reason}"
        for cr in val_result.citation_results
        if not cr.is_valid and cr.error_reason
    ]

    return total_citations, valid, rate, errors


def compute_recall_at_k(
    retrieved_files: list[str],
    expected_files: list[str],
    k_values: list[int] | None = None,
) -> tuple[dict[int, float], dict[int, float]]:
    """Compute Recall@k and Hit@k for given retrieved files and ground truth expected files.

    File paths are normalized to lowercase forward slashes before comparison.
    """
    if k_values is None:
        k_values = [1, 3, 5, 10]

    normalized_retrieved: list[str] = []
    seen: set[str] = set()
    for f in retrieved_files:
        norm = f.strip().replace("\\", "/").lower()
        if norm and norm not in seen:
            seen.add(norm)
            normalized_retrieved.append(norm)

    normalized_expected = {
        f.strip().replace("\\", "/").lower()
        for f in expected_files
        if f.strip()
    }

    recall_at_k: dict[int, float] = {}
    hit_at_k: dict[int, float] = {}

    if not normalized_expected:
        # If no expected files recorded for this case, treat as full match
        for k in k_values:
            recall_at_k[k] = 1.0
            hit_at_k[k] = 1.0
        return recall_at_k, hit_at_k

    for k in k_values:
        top_k = set(normalized_retrieved[:k])
        matching = top_k & normalized_expected
        recall_at_k[k] = len(matching) / len(normalized_expected)
        hit_at_k[k] = 1.0 if matching else 0.0

    return recall_at_k, hit_at_k


def compute_unsupported_claims(
    report: InvestigationReport,
    snapshot: RepoSnapshot | None = None,
) -> tuple[int, list[str]]:
    """Count unsupported claims and ungrounded hypotheses in an InvestigationReport.

    A claim is deemed unsupported if:
    1. A hypothesis has zero supporting evidence IDs.
    2. A hypothesis references evidence IDs that do not exist in the report.
    3. When a snapshot is provided, a hypothesis references invalid evidence items.

    Returns:
        (unsupported_claims_count, reasons)
    """
    evidence_ids = {item.evidence_id for item in report.evidence}
    unsupported_count = 0
    reasons: list[str] = []

    valid_evidence_ids: set[str] | None = None
    if snapshot is not None:
        _, val_result = validate_investigation_report(report, snapshot)
        valid_evidence_ids = set(val_result.valid_evidence_ids)

    for idx, hyp in enumerate(report.hypotheses, start=1):
        hyp_label = f"Hypothesis #{idx} ('{hyp.title[:40]}...')"
        if not hyp.supporting_evidence_ids:
            unsupported_count += 1
            reasons.append(f"{hyp_label} has no supporting evidence IDs")
            continue

        for eid in hyp.supporting_evidence_ids:
            if eid not in evidence_ids:
                unsupported_count += 1
                reasons.append(
                    f"{hyp_label} references non-existent evidence ID '{eid}'"
                )
            elif valid_evidence_ids is not None and eid not in valid_evidence_ids:
                unsupported_count += 1
                reasons.append(
                    f"{hyp_label} references invalid/unverified evidence ID '{eid}'"
                )

    return unsupported_count, reasons


def score_root_cause_alignment(
    report: InvestigationReport,
    known_resolution: str,
) -> tuple[str, float]:
    """Score the alignment between report hypotheses and the ground-truth known resolution.

    Returns:
        (category, alignment_score)
        category is one of: 'correct', 'partially_useful', 'incorrect', 'insufficient_evidence'
        alignment_score is a normalized score between 0.0 and 1.0.
    """
    if (
        (report.verification_status == "not_run" or not report.hypotheses)
        and any("insufficient" in lim.lower() for lim in report.limitations)
    ):
        return "insufficient_evidence", 0.0

    # Extract keywords from known resolution
    truth_tokens = {
        token.lower()
        for token in _WORD_REGEX.findall(known_resolution)
        if token.lower() not in _STOPWORDS
    }

    if not truth_tokens:
        return "partially_useful", 0.5

    # Aggregate tokens across hypotheses (weighted by rank)
    best_category = "incorrect"
    best_score = 0.0

    for idx, hyp in enumerate(report.hypotheses):
        text = f"{hyp.title} {hyp.explanation}"
        hyp_tokens = {
            token.lower()
            for token in _WORD_REGEX.findall(text)
            if token.lower() not in _STOPWORDS
        }
        intersection = truth_tokens & hyp_tokens
        jaccard = len(intersection) / len(truth_tokens) if truth_tokens else 0.0

        # Rank discount (hyp 1 has 1.0 weight, hyp 2 has 0.8, etc.)
        rank_weight = max(0.5, 1.0 - (idx * 0.2))
        weighted_score = min(1.0, jaccard * rank_weight * 1.5)

        if weighted_score > best_score:
            best_score = weighted_score

    if best_score >= 0.35:
        best_category = "correct"
    elif best_score >= 0.15:
        best_category = "partially_useful"
    else:
        best_category = "incorrect"

    return best_category, round(best_score, 4)


class CaseEvaluationMetrics(BaseModel):
    """Metrics outcome for a single evaluated benchmark case."""

    case_id: str = Field(..., description="Unique case identifier")
    repo_full_name: str = Field(..., description="Repository full name")
    issue_number: int = Field(..., description="GitHub issue number")
    target_commit_sha: str = Field(..., description="Pinned pre-fix commit SHA")
    status: str = Field(..., description="Execution status (completed, failed, etc.)")
    total_code_citations: int = Field(default=0)
    valid_code_citations: int = Field(default=0)
    citation_validity_rate: float = Field(default=1.0)
    recall_at_k: dict[int, float] = Field(default_factory=dict)
    hit_at_k: dict[int, float] = Field(default_factory=dict)
    retrieved_files: list[str] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    unsupported_claims_count: int = Field(default=0)
    unsupported_claim_reasons: list[str] = Field(default_factory=list)
    root_cause_alignment: str = Field(default="incorrect")
    alignment_score: float = Field(default=0.0)
    latency_seconds: float = Field(default=0.0)
    error_reason: str | None = Field(default=None)


class AggregatedEvaluationReport(BaseModel):
    """Aggregated benchmark report summarizing performance across all evaluated cases."""

    total_cases: int = Field(..., description="Total evaluated cases in dataset")
    completed_cases: int = Field(..., description="Number of successfully completed runs")
    failed_cases: int = Field(..., description="Number of failed/errored runs")
    insufficient_evidence_cases: int = Field(..., description="Cases with insufficient evidence")
    mean_citation_validity: float = Field(default=1.0)
    mean_recall_at_k: dict[int, float] = Field(default_factory=dict)
    mean_hit_at_k: dict[int, float] = Field(default_factory=dict)
    mean_unsupported_claims: float = Field(default=0.0)
    mean_latency_seconds: float = Field(default=0.0)
    root_cause_distribution: dict[str, int] = Field(default_factory=dict)
    benchmark_targets_met: dict[str, bool] = Field(default_factory=dict)
    case_metrics: list[CaseEvaluationMetrics] = Field(default_factory=list)
    failure_notes: list[dict[str, Any]] = Field(default_factory=list)


def compute_evaluation_metrics(
    case: EvaluationCaseModel,
    report: InvestigationReport | None,
    snapshot: RepoSnapshot | None = None,
    latency_seconds: float = 0.0,
    error_reason: str | None = None,
    k_values: list[int] | None = None,
) -> CaseEvaluationMetrics:
    """Compute all evaluation metrics for an individual benchmark case."""
    if k_values is None:
        k_values = [1, 3, 5, 10]

    if report is None:
        # Failed execution case
        recall_at_k = dict.fromkeys(k_values, 0.0)
        hit_at_k = dict.fromkeys(k_values, 0.0)
        return CaseEvaluationMetrics(
            case_id=case.id,
            repo_full_name=case.repo_full_name,
            issue_number=case.issue_number,
            target_commit_sha=case.target_commit_sha,
            status="failed",
            total_code_citations=0,
            valid_code_citations=0,
            citation_validity_rate=0.0,
            recall_at_k=recall_at_k,
            hit_at_k=hit_at_k,
            retrieved_files=[],
            expected_files=case.expected_files,
            unsupported_claims_count=0,
            root_cause_alignment="incorrect",
            alignment_score=0.0,
            latency_seconds=latency_seconds,
            error_reason=error_reason or "Investigation failed to generate report",
        )

    # 1. Citation validity
    tot_cit, val_cit, cit_rate, _ = compute_citation_validity(report, snapshot)

    # 2. Extract retrieved files
    retrieved_files: list[str] = []
    seen: set[str] = set()
    for ev in report.evidence:
        if ev.source_type in ("code", "documentation") and ev.path_or_url:
            norm = ev.path_or_url.replace("\\", "/")
            if norm not in seen:
                seen.add(norm)
                retrieved_files.append(norm)

    # 3. Recall@k and Hit@k
    recall_at_k, hit_at_k = compute_recall_at_k(
        retrieved_files=retrieved_files,
        expected_files=case.expected_files,
        k_values=k_values,
    )

    # 4. Unsupported claims
    unsupported_count, unsupported_reasons = compute_unsupported_claims(report, snapshot)

    # 5. Root cause alignment
    alignment_category, alignment_score = score_root_cause_alignment(
        report=report,
        known_resolution=case.known_resolution,
    )

    status = "completed"
    if report.verification_status == "not_run" or not report.hypotheses:
        status = "insufficient_evidence"

    return CaseEvaluationMetrics(
        case_id=case.id,
        repo_full_name=case.repo_full_name,
        issue_number=case.issue_number,
        target_commit_sha=case.target_commit_sha,
        status=status,
        total_code_citations=tot_cit,
        valid_code_citations=val_cit,
        citation_validity_rate=round(cit_rate, 4),
        recall_at_k={k: round(v, 4) for k, v in recall_at_k.items()},
        hit_at_k={k: round(v, 4) for k, v in hit_at_k.items()},
        retrieved_files=retrieved_files,
        expected_files=case.expected_files,
        unsupported_claims_count=unsupported_count,
        unsupported_claim_reasons=unsupported_reasons,
        root_cause_alignment=alignment_category,
        alignment_score=alignment_score,
        latency_seconds=round(latency_seconds, 3),
        error_reason=error_reason,
    )


def aggregate_evaluation_metrics(
    metrics_list: list[CaseEvaluationMetrics],
    k_values: list[int] | None = None,
) -> AggregatedEvaluationReport:
    """Aggregate individual case metrics into a benchmark evaluation report."""
    if k_values is None:
        k_values = [1, 3, 5, 10]

    total_cases = len(metrics_list)
    if total_cases == 0:
        return AggregatedEvaluationReport(
            total_cases=0,
            completed_cases=0,
            failed_cases=0,
            insufficient_evidence_cases=0,
            mean_citation_validity=1.0,
            mean_recall_at_k=dict.fromkeys(k_values, 0.0),
            mean_hit_at_k=dict.fromkeys(k_values, 0.0),
            mean_unsupported_claims=0.0,
            mean_latency_seconds=0.0,
            root_cause_distribution={},
            benchmark_targets_met={
                "citation_validity_ge_95": True,
                "recall_at_5_ge_80": False,
                "unsupported_claims_lt_0_5": True,
            },
            case_metrics=[],
            failure_notes=[],
        )

    completed_cases = sum(1 for m in metrics_list if m.status == "completed")
    failed_cases = sum(1 for m in metrics_list if m.status == "failed")
    insufficient_cases = sum(1 for m in metrics_list if m.status == "insufficient_evidence")

    # Means across all cases
    mean_cit_validity = sum(m.citation_validity_rate for m in metrics_list) / total_cases
    mean_unsupported = sum(m.unsupported_claims_count for m in metrics_list) / total_cases
    mean_latency = sum(m.latency_seconds for m in metrics_list) / total_cases

    mean_recall: dict[int, float] = {}
    mean_hit: dict[int, float] = {}
    for k in k_values:
        mean_recall[k] = round(
            sum(m.recall_at_k.get(k, 0.0) for m in metrics_list) / total_cases, 4
        )
        mean_hit[k] = round(
            sum(m.hit_at_k.get(k, 0.0) for m in metrics_list) / total_cases, 4
        )

    rc_counter = Counter(m.root_cause_alignment for m in metrics_list)

    # Check against spec targets (Section 9 & Architecture):
    # Citation validity >= 0.95
    # Recall@5 >= 0.80
    # Unsupported claims < 0.5
    targets_met = {
        "citation_validity_ge_95": mean_cit_validity >= 0.95,
        "recall_at_5_ge_80": mean_recall.get(5, 0.0) >= 0.80,
        "unsupported_claims_lt_0_5": mean_unsupported < 0.5,
    }

    failure_notes: list[dict[str, Any]] = []
    for m in metrics_list:
        if m.status in ("failed", "insufficient_evidence") or m.citation_validity_rate < 0.95:
            failure_notes.append({
                "case_id": m.case_id,
                "repo": m.repo_full_name,
                "status": m.status,
                "citation_validity": m.citation_validity_rate,
                "recall_at_5": m.recall_at_k.get(5, 0.0),
                "unsupported_claims": m.unsupported_claims_count,
                "error_reason": m.error_reason,
            })

    return AggregatedEvaluationReport(
        total_cases=total_cases,
        completed_cases=completed_cases,
        failed_cases=failed_cases,
        insufficient_evidence_cases=insufficient_cases,
        mean_citation_validity=round(mean_cit_validity, 4),
        mean_recall_at_k=mean_recall,
        mean_hit_at_k=mean_hit,
        mean_unsupported_claims=round(mean_unsupported, 4),
        mean_latency_seconds=round(mean_latency, 3),
        root_cause_distribution=dict(rc_counter),
        benchmark_targets_met=targets_met,
        case_metrics=metrics_list,
        failure_notes=failure_notes,
    )
