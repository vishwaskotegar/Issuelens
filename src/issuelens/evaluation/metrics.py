"""Evaluation metrics for the IssueLens retrospective benchmark.

Design notes (post-build audit, see ADR-013):

* Metrics measure the *agent's* behaviour, not the system's own plumbing. The LLM does
  not write file paths or excerpts; it chooses evidence IDs. So citation validity is the
  share of IDs the LLM cited that survived validation, and unsupported claims are
  hypotheses the validator could not ground in repository evidence.
* Retrieval is reported twice: ``search_*`` scores the ranked list of every file the
  search tools returned, ``diagnosis_hit_at_k`` scores the files the hypotheses actually
  cite, in hypothesis rank order.
* Root-cause alignment is file based (does the diagnosis cite a file the fix changed?).
  The former keyword-overlap score is kept only as a diagnostic next to an
  "echo the issue" baseline, because restating the issue text scored as well as the agent.
* Failed runs are excluded from quality means and reported as a completion rate.
"""

import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from issuelens.agent.report import InvestigationReport
from issuelens.core.logging import get_logger
from issuelens.evaluation.dataset import EvaluationCaseModel

logger = get_logger("issuelens.evaluation.metrics")

_WORD_REGEX = re.compile(r"\b[a-zA-Z_]\w{2,}\b")
_STOPWORDS = {
    "the", "and", "for", "that", "this", "with", "from", "when", "where", "which",
    "will", "would", "should", "could", "have", "been", "fixed", "issue", "error",
    "bug", "code", "file", "function", "class", "module", "using", "into", "some",
}

REPOSITORY_SOURCE_TYPES = ("code", "documentation")

# Benchmark targets (docs/BUILD_SPECIFICATION.md Section 9 / evals/README.md Section 4).
TARGET_CITATION_VALIDITY = 0.95
TARGET_SEARCH_RECALL_AT_5 = 0.80
TARGET_SEARCH_HIT_AT_5 = 0.90
TARGET_UNSUPPORTED_CLAIMS = 0.5
TARGET_ROOT_CAUSE_USEFUL = 0.70


def _normalize_path(path: str) -> str:
    return path.strip().replace("\\", "/").lower()


def compute_citation_validity(
    report: InvestigationReport,
) -> tuple[int, int, float | None, list[str]]:
    """Share of evidence IDs cited by the LLM's hypotheses that passed validation.

    Counts ``supporting_evidence_ids`` (kept by the validator) plus
    ``rejected_evidence_ids`` (removed by it). Returns ``None`` for the rate when the LLM
    cited nothing: "no citations" is not "100% valid citations".

    Returns:
        (cited_total, cited_valid, validity_rate_or_None, reasons)
    """
    cited_total = 0
    cited_valid = 0
    reasons: list[str] = []
    for idx, hyp in enumerate(report.hypotheses, start=1):
        cited_valid += len(hyp.supporting_evidence_ids)
        cited_total += len(hyp.supporting_evidence_ids) + len(hyp.rejected_evidence_ids)
        for eid in hyp.rejected_evidence_ids:
            reasons.append(f"Hypothesis #{idx} cited '{eid}', which failed validation")
    if cited_total == 0:
        return 0, 0, None, reasons
    return cited_total, cited_valid, cited_valid / cited_total, reasons


def compute_recall_at_k(
    retrieved_files: list[str],
    expected_files: list[str],
    k_values: list[int] | None = None,
) -> tuple[dict[int, float], dict[int, float]]:
    """Compute Recall@k and Hit@k for a ranked file list against expected files.

    File paths are normalized to lowercase forward slashes before comparison. A case with
    no expected files scores 0.0 (it cannot be used to measure retrieval).
    """
    if k_values is None:
        k_values = [1, 3, 5, 10]

    normalized_retrieved: list[str] = []
    seen: set[str] = set()
    for f in retrieved_files:
        norm = _normalize_path(f)
        if norm and norm not in seen:
            seen.add(norm)
            normalized_retrieved.append(norm)

    normalized_expected = {_normalize_path(f) for f in expected_files if f.strip()}

    recall_at_k: dict[int, float] = {}
    hit_at_k: dict[int, float] = {}
    for k in k_values:
        if not normalized_expected:
            recall_at_k[k] = 0.0
            hit_at_k[k] = 0.0
            continue
        matching = set(normalized_retrieved[:k]) & normalized_expected
        recall_at_k[k] = len(matching) / len(normalized_expected)
        hit_at_k[k] = 1.0 if matching else 0.0
    return recall_at_k, hit_at_k


def retrieved_files_in_search_order(report: InvestigationReport) -> list[str]:
    """Every repository file in the report's evidence, in retrieval order."""
    files: list[str] = []
    for ev in report.evidence:
        if ev.source_type in REPOSITORY_SOURCE_TYPES and ev.path_or_url:
            files.append(ev.path_or_url.replace("\\", "/"))
    return list(dict.fromkeys(files))


def cited_files_in_rank_order(report: InvestigationReport) -> list[str]:
    """Repository files cited as support by hypotheses, ordered by hypothesis rank."""
    by_id = {ev.evidence_id: ev for ev in report.evidence}
    files: list[str] = []
    for hyp in report.hypotheses:
        for eid in hyp.supporting_evidence_ids:
            ev = by_id.get(eid)
            if ev is not None and ev.source_type in REPOSITORY_SOURCE_TYPES:
                files.append(ev.path_or_url.replace("\\", "/"))
    return list(dict.fromkeys(files))


def _is_supported(hyp: Any) -> bool:
    status = getattr(hyp, "support_status", None)
    if status is not None:
        return bool(status == "supported")
    return bool(hyp.supporting_evidence_ids)


def compute_unsupported_claims(report: InvestigationReport) -> tuple[int, list[str]]:
    """Count hypotheses the validator could not ground in repository evidence.

    Uses the validator-computed ``support_status``; reports produced without validation
    (``support_status`` is None) fall back to "has at least one supporting ID".
    """
    count = 0
    reasons: list[str] = []
    for idx, hyp in enumerate(report.hypotheses, start=1):
        if not _is_supported(hyp):
            count += 1
            reasons.append(
                f"Hypothesis #{idx} ('{hyp.title[:40]}') is not supported by verified "
                "repository evidence"
            )
    return count, reasons


def score_root_cause_alignment(
    report: InvestigationReport,
    expected_files: list[str],
) -> str:
    """File-based root-cause alignment against the files the real fix changed.

    * ``correct``: the top-ranked hypothesis is supported and cites an expected file.
    * ``partially_useful``: some supported hypothesis cites an expected file.
    * ``insufficient_evidence``: no hypothesis is supported by repository evidence.
    * ``incorrect``: otherwise.

    This rewards pointing at the right code, not wording. It cannot tell whether the
    explanation is right; that needs human or LLM-judge review (not implemented).
    """
    expected = {_normalize_path(f) for f in expected_files if f.strip()}
    by_id = {ev.evidence_id: ev for ev in report.evidence}

    def cited(hyp: Any) -> set[str]:
        return {
            _normalize_path(by_id[eid].path_or_url)
            for eid in hyp.supporting_evidence_ids
            if eid in by_id and by_id[eid].source_type in REPOSITORY_SOURCE_TYPES
        }

    supported = [h for h in report.hypotheses if _is_supported(h)]
    if not supported:
        return "insufficient_evidence"
    if _is_supported(report.hypotheses[0]) and cited(report.hypotheses[0]) & expected:
        return "correct"
    if any(cited(h) & expected for h in supported):
        return "partially_useful"
    return "incorrect"


def keyword_overlap_score(text: str, known_resolution: str) -> float:
    """Diagnostic only: fraction of fix-title keywords that appear in ``text``.

    Kept to show why it is not used as a quality metric: an issue's own text usually
    scores as high as a correct diagnosis.
    """
    truth = {
        t.lower() for t in _WORD_REGEX.findall(known_resolution) if t.lower() not in _STOPWORDS
    }
    if not truth:
        return 0.0
    words = {t.lower() for t in _WORD_REGEX.findall(text) if t.lower() not in _STOPWORDS}
    return round(len(truth & words) / len(truth), 4)


class CaseEvaluationMetrics(BaseModel):
    """Metrics outcome for a single evaluated benchmark case."""

    case_id: str = Field(..., description="Unique case identifier")
    repo_full_name: str = Field(..., description="Repository full name")
    issue_number: int = Field(..., description="GitHub issue number")
    target_commit_sha: str = Field(..., description="Pinned pre-fix commit SHA")
    status: str = Field(
        ...,
        description="completed | insufficient_evidence | failed | snapshot_unavailable",
    )
    cited_evidence_total: int = Field(default=0, description="Evidence IDs the LLM cited")
    cited_evidence_valid: int = Field(default=0, description="Cited IDs that passed validation")
    citation_validity_rate: float | None = Field(
        default=None, description="cited_valid / cited_total; None when nothing was cited"
    )
    search_recall_at_k: dict[int, float] = Field(default_factory=dict)
    search_hit_at_k: dict[int, float] = Field(default_factory=dict)
    diagnosis_hit_at_k: dict[int, float] = Field(default_factory=dict)
    retrieved_files: list[str] = Field(default_factory=list)
    cited_files: list[str] = Field(default_factory=list)
    expected_files: list[str] = Field(default_factory=list)
    hypotheses_count: int = Field(default=0)
    unsupported_claims_count: int = Field(default=0)
    unsupported_claim_reasons: list[str] = Field(default_factory=list)
    root_cause_alignment: str = Field(default="incorrect")
    keyword_overlap_score: float = Field(
        default=0.0, description="Diagnostic only: fix-title keyword overlap of hypotheses"
    )
    issue_echo_keyword_overlap: float = Field(
        default=0.0, description="Diagnostic baseline: same score for the raw issue text"
    )
    latency_seconds: float = Field(default=0.0)
    error_reason: str | None = Field(default=None)


class AggregatedEvaluationReport(BaseModel):
    """Aggregated benchmark report summarizing performance across all evaluated cases."""

    total_cases: int = Field(..., description="Total evaluated cases in dataset")
    completed_cases: int = Field(..., description="Runs that produced a supported diagnosis")
    insufficient_evidence_cases: int = Field(..., description="Runs with no supported hypothesis")
    failed_cases: int = Field(..., description="Runs that errored or had no snapshot")
    scored_cases: int = Field(
        ..., description="Runs that produced a report (quality means use these)"
    )
    completion_rate: float = Field(default=0.0, description="scored_cases / total_cases")
    cases_with_citations: int = Field(default=0)
    mean_citation_validity: float | None = Field(default=None)
    mean_search_recall_at_k: dict[int, float] = Field(default_factory=dict)
    mean_search_hit_at_k: dict[int, float] = Field(default_factory=dict)
    mean_diagnosis_hit_at_k: dict[int, float] = Field(default_factory=dict)
    mean_unsupported_claims: float = Field(default=0.0)
    root_cause_useful_rate: float = Field(
        default=0.0, description="(correct + partially_useful) / scored_cases"
    )
    mean_keyword_overlap: float = Field(default=0.0)
    mean_issue_echo_keyword_overlap: float = Field(default=0.0)
    mean_latency_seconds: float = Field(default=0.0)
    root_cause_distribution: dict[str, int] = Field(default_factory=dict)
    benchmark_targets_met: dict[str, bool] = Field(default_factory=dict)
    case_metrics: list[CaseEvaluationMetrics] = Field(default_factory=list)
    failure_notes: list[dict[str, Any]] = Field(default_factory=list)


def compute_evaluation_metrics(
    case: EvaluationCaseModel,
    report: InvestigationReport | None,
    latency_seconds: float = 0.0,
    error_reason: str | None = None,
    k_values: list[int] | None = None,
    failure_status: str = "failed",
) -> CaseEvaluationMetrics:
    """Compute all evaluation metrics for an individual benchmark case."""
    if k_values is None:
        k_values = [1, 3, 5, 10]

    issue_text = f"{case.issue_title or ''} {case.issue_body or ''}"
    echo_score = keyword_overlap_score(issue_text, case.known_resolution)

    if report is None:
        zeros = dict.fromkeys(k_values, 0.0)
        return CaseEvaluationMetrics(
            case_id=case.id,
            repo_full_name=case.repo_full_name,
            issue_number=case.issue_number,
            target_commit_sha=case.target_commit_sha,
            status=failure_status,
            search_recall_at_k=zeros,
            search_hit_at_k=dict(zeros),
            diagnosis_hit_at_k=dict(zeros),
            expected_files=case.expected_files,
            root_cause_alignment="failed",
            issue_echo_keyword_overlap=echo_score,
            latency_seconds=round(latency_seconds, 3),
            error_reason=error_reason or "Investigation failed to generate report",
        )

    cited_total, cited_valid, cit_rate, _ = compute_citation_validity(report)
    retrieved = retrieved_files_in_search_order(report)
    cited_files = cited_files_in_rank_order(report)
    search_recall, search_hit = compute_recall_at_k(retrieved, case.expected_files, k_values)
    _, diagnosis_hit = compute_recall_at_k(cited_files, case.expected_files, k_values)
    unsupported_count, unsupported_reasons = compute_unsupported_claims(report)
    alignment = score_root_cause_alignment(report, case.expected_files)
    hyp_text = " ".join(f"{h.title} {h.explanation}" for h in report.hypotheses)

    return CaseEvaluationMetrics(
        case_id=case.id,
        repo_full_name=case.repo_full_name,
        issue_number=case.issue_number,
        target_commit_sha=case.target_commit_sha,
        status="insufficient_evidence" if alignment == "insufficient_evidence" else "completed",
        cited_evidence_total=cited_total,
        cited_evidence_valid=cited_valid,
        citation_validity_rate=None if cit_rate is None else round(cit_rate, 4),
        search_recall_at_k={k: round(v, 4) for k, v in search_recall.items()},
        search_hit_at_k={k: round(v, 4) for k, v in search_hit.items()},
        diagnosis_hit_at_k={k: round(v, 4) for k, v in diagnosis_hit.items()},
        retrieved_files=retrieved,
        cited_files=cited_files,
        expected_files=case.expected_files,
        hypotheses_count=len(report.hypotheses),
        unsupported_claims_count=unsupported_count,
        unsupported_claim_reasons=unsupported_reasons,
        root_cause_alignment=alignment,
        keyword_overlap_score=keyword_overlap_score(hyp_text, case.known_resolution),
        issue_echo_keyword_overlap=echo_score,
        latency_seconds=round(latency_seconds, 3),
        error_reason=error_reason,
    )


def _mean(values: list[float]) -> float:
    return round(sum(values) / len(values), 4) if values else 0.0


def aggregate_evaluation_metrics(
    metrics_list: list[CaseEvaluationMetrics],
    k_values: list[int] | None = None,
) -> AggregatedEvaluationReport:
    """Aggregate case metrics. Quality means are over cases that produced a report."""
    if k_values is None:
        k_values = [1, 3, 5, 10]

    total = len(metrics_list)
    scored = [m for m in metrics_list if m.status in ("completed", "insufficient_evidence")]
    failed = [m for m in metrics_list if m not in scored]
    with_citations = [m for m in scored if m.citation_validity_rate is not None]

    mean_cit: float | None = (
        _mean([m.citation_validity_rate or 0.0 for m in with_citations])
        if with_citations
        else None
    )
    mean_unsupported = _mean([float(m.unsupported_claims_count) for m in scored])

    def mean_at_k(attr: str) -> dict[int, float]:
        return {k: _mean([getattr(m, attr).get(k, 0.0) for m in scored]) for k in k_values}

    search_recall = mean_at_k("search_recall_at_k")
    search_hit = mean_at_k("search_hit_at_k")
    diagnosis_hit = mean_at_k("diagnosis_hit_at_k")

    rc_counter = Counter(m.root_cause_alignment for m in metrics_list)
    useful = sum(1 for m in scored if m.root_cause_alignment in ("correct", "partially_useful"))
    useful_rate = round(useful / len(scored), 4) if scored else 0.0

    targets_met = {
        "citation_validity_ge_95": mean_cit is not None and mean_cit >= TARGET_CITATION_VALIDITY,
        "search_recall_at_5_ge_80": search_recall.get(5, 0.0) >= TARGET_SEARCH_RECALL_AT_5,
        "search_hit_at_5_ge_90": search_hit.get(5, 0.0) >= TARGET_SEARCH_HIT_AT_5,
        "unsupported_claims_lt_0_5": bool(scored) and mean_unsupported < TARGET_UNSUPPORTED_CLAIMS,
        "root_cause_useful_ge_70": useful_rate >= TARGET_ROOT_CAUSE_USEFUL,
    }

    failure_notes: list[dict[str, Any]] = [
        {
            "case_id": m.case_id,
            "repo": m.repo_full_name,
            "status": m.status,
            "root_cause_alignment": m.root_cause_alignment,
            "citation_validity": m.citation_validity_rate,
            "search_recall_at_5": m.search_recall_at_k.get(5, 0.0),
            "unsupported_claims": m.unsupported_claims_count,
            "error_reason": m.error_reason,
        }
        for m in metrics_list
        if m.status != "completed"
        or (m.citation_validity_rate is not None and m.citation_validity_rate < 0.95)
    ]

    return AggregatedEvaluationReport(
        total_cases=total,
        completed_cases=sum(1 for m in scored if m.status == "completed"),
        insufficient_evidence_cases=sum(1 for m in scored if m.status == "insufficient_evidence"),
        failed_cases=len(failed),
        scored_cases=len(scored),
        completion_rate=round(len(scored) / total, 4) if total else 0.0,
        cases_with_citations=len(with_citations),
        mean_citation_validity=mean_cit,
        mean_search_recall_at_k=search_recall,
        mean_search_hit_at_k=search_hit,
        mean_diagnosis_hit_at_k=diagnosis_hit,
        mean_unsupported_claims=mean_unsupported,
        root_cause_useful_rate=useful_rate,
        mean_keyword_overlap=_mean([m.keyword_overlap_score for m in scored]),
        mean_issue_echo_keyword_overlap=_mean([m.issue_echo_keyword_overlap for m in scored]),
        mean_latency_seconds=round(
            sum(m.latency_seconds for m in metrics_list) / total, 3
        )
        if total
        else 0.0,
        root_cause_distribution=dict(rc_counter),
        benchmark_targets_met=targets_met,
        case_metrics=metrics_list,
        failure_notes=failure_notes,
    )
