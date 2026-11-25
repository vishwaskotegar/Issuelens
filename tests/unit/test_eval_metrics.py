"""Unit tests for the evaluation metrics engine (metrics version 2, post-build audit).

Each metric is tested for the property it claims to measure, including inputs that
should make it score badly. The previous metrics could not fail: citation validity
re-checked an already-filtered report, unsupported claims were hidden by auto-grounding,
and keyword overlap rated a restated issue as a correct diagnosis.
"""

from issuelens.agent.report import (
    EvidenceItem,
    Hypothesis,
    InvestigationReport,
    VerificationStatus,
)
from issuelens.evaluation.dataset import EvaluationCaseModel
from issuelens.evaluation.metrics import (
    CaseEvaluationMetrics,
    aggregate_evaluation_metrics,
    cited_files_in_rank_order,
    compute_citation_validity,
    compute_evaluation_metrics,
    compute_recall_at_k,
    compute_unsupported_claims,
    keyword_overlap_score,
    retrieved_files_in_search_order,
    score_root_cause_alignment,
)

SAMPLE_SHA = "0123456789abcdef0123456789abcdef01234567"


def _ev(eid: str, path: str, source_type: str = "code") -> EvidenceItem:
    return EvidenceItem(
        evidence_id=eid,
        source_type=source_type,  # type: ignore[arg-type]
        path_or_url=path,
        start_line=1,
        end_line=2,
        excerpt="x = 1",
        commit_sha=SAMPLE_SHA,
    )


def _hyp(
    title: str,
    cited: list[str] | None = None,
    rejected: list[str] | None = None,
    support_status: str | None = "supported",
    explanation: str = "Mechanism.",
) -> Hypothesis:
    return Hypothesis(
        title=title,
        explanation=explanation,
        confidence="medium",
        supporting_evidence_ids=cited or [],
        rejected_evidence_ids=rejected or [],
        support_status=support_status,  # type: ignore[arg-type]
    )


def _report(
    evidence: list[EvidenceItem],
    hypotheses: list[Hypothesis],
    verification_status: VerificationStatus = "verified",
) -> InvestigationReport:
    return InvestigationReport(
        run_id="run_test_001",
        issue_url="https://github.com/sample/repo/issues/100",
        repository="sample/repo",
        commit_sha=SAMPLE_SHA,
        summary="Summary.",
        observed_facts=[],
        hypotheses=hypotheses,
        evidence=evidence,
        missing_information=[],
        suggested_tests=[],
        recommended_next_steps=[],
        verification_status=verification_status,
        limitations=[],
    )


def _case(
    expected: list[str], title: str = "Token check fails", body: str = ""
) -> EvaluationCaseModel:
    return EvaluationCaseModel(
        id="CASE-T",
        issue_url="https://github.com/sample/repo/issues/100",
        repo_full_name="sample/repo",
        issue_number=100,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="fix(auth): reject empty tokens in validate_token",
        expected_files=expected,
        issue_title=title,
        issue_body=body,
    )


# --- citation validity ------------------------------------------------------------


def test_citation_validity_is_none_when_nothing_was_cited() -> None:
    """No citations is not 100% valid citations."""
    total, valid, rate, _ = compute_citation_validity(_report([], [_hyp("H", cited=[])]))
    assert (total, valid, rate) == (0, 0, None)


def test_citation_validity_counts_rejected_llm_citations() -> None:
    """Regression: validity was computed on the already-filtered report, so it was always 1.0."""
    report = _report([_ev("ev_1", "a.py")], [_hyp("H", cited=["ev_1"], rejected=["ev_9"])])
    total, valid, rate, reasons = compute_citation_validity(report)
    assert (total, valid, rate) == (2, 1, 0.5)
    assert any("ev_9" in r for r in reasons)


# --- retrieval ---------------------------------------------------------------------


def test_recall_and_hit_at_k() -> None:
    recall, hit = compute_recall_at_k(
        ["a.py", "b.py", "c.py", "d.py"], ["c.py", "z.py"], k_values=[1, 3, 5]
    )
    assert recall == {1: 0.0, 3: 0.5, 5: 0.5}
    assert hit == {1: 0.0, 3: 1.0, 5: 1.0}


def test_recall_with_no_expected_files_is_zero_not_perfect() -> None:
    """Regression: a case without ground-truth files used to score a perfect 1.0."""
    recall, hit = compute_recall_at_k(["a.py"], [], k_values=[5])
    assert recall == {5: 0.0} and hit == {5: 0.0}


def test_search_order_and_cited_order_are_distinct() -> None:
    """Search recall scores the tool's ranking; diagnosis hit scores what the agent cites."""
    evidence = [
        _ev("ev_1", "noise.py"), _ev("ev_2", "fix.py"), _ev("doc_ev_3", "issue:1", "issue")
    ]
    report = _report(evidence, [_hyp("H1", cited=["ev_2"]), _hyp("H2", cited=["doc_ev_3"])])
    assert retrieved_files_in_search_order(report) == ["noise.py", "fix.py"]
    # Issue evidence is not a repository file and never counts as a cited file.
    assert cited_files_in_rank_order(report) == ["fix.py"]


# --- unsupported claims -------------------------------------------------------------


def test_unsupported_claims_use_validator_support_status() -> None:
    """A hypothesis the validator marked unsupported counts, even if it lists an ID
    (e.g. one supported only by issue text)."""
    report = _report(
        [_ev("ev_1", "a.py")],
        [
            _hyp("ok", cited=["ev_1"], support_status="supported"),
            _hyp("issue only", cited=["doc_ev_2"], support_status="unsupported"),
            _hyp("uncited", cited=[], support_status="unsupported"),
        ],
    )
    count, reasons = compute_unsupported_claims(report)
    assert count == 2
    assert len(reasons) == 2


def test_unsupported_claims_fallback_without_support_status() -> None:
    report = _report([], [_hyp("x", cited=[], support_status=None)])
    assert compute_unsupported_claims(report)[0] == 1


# --- root-cause alignment -------------------------------------------------------------


def test_root_cause_correct_when_top_hypothesis_cites_a_fixed_file() -> None:
    report = _report([_ev("ev_1", "pkg/auth.py")], [_hyp("H", cited=["ev_1"])])
    assert score_root_cause_alignment(report, ["pkg/auth.py"]) == "correct"


def test_root_cause_partial_when_lower_ranked_hypothesis_cites_it() -> None:
    report = _report(
        [_ev("ev_1", "pkg/db.py"), _ev("ev_2", "pkg/auth.py")],
        [_hyp("H1", cited=["ev_1"]), _hyp("H2", cited=["ev_2"])],
    )
    assert score_root_cause_alignment(report, ["pkg/auth.py"]) == "partially_useful"


def test_root_cause_restating_the_issue_is_not_correct() -> None:
    """Regression: keyword overlap scored an echo of the issue/fix wording as 'correct'.

    A hypothesis that repeats the fix title word-for-word but cites the wrong file is
    incorrect under the file-based metric.
    """
    case = _case(["pkg/auth.py"])
    report = _report(
        [_ev("ev_1", "pkg/db.py")],
        [_hyp(case.known_resolution, cited=["ev_1"], explanation=case.known_resolution)],
    )
    assert score_root_cause_alignment(report, case.expected_files) == "incorrect"
    assert keyword_overlap_score(case.known_resolution, case.known_resolution) == 1.0


def test_root_cause_insufficient_when_nothing_is_supported() -> None:
    report = _report(
        [_ev("ev_1", "pkg/auth.py")], [_hyp("H", cited=[], support_status="unsupported")]
    )
    assert score_root_cause_alignment(report, ["pkg/auth.py"]) == "insufficient_evidence"


# --- per-case and aggregate ---------------------------------------------------------


def test_compute_evaluation_metrics_full() -> None:
    evidence = [_ev("ev_1", "pkg/noise.py"), _ev("ev_2", "pkg/auth.py")]
    report = _report(evidence, [_hyp("H", cited=["ev_2"], rejected=["ev_7"])])
    m = compute_evaluation_metrics(
        _case(["pkg/auth.py"], title="validate_token accepts empty tokens"),
        report,
        k_values=[1, 5],
    )
    assert m.status == "completed"
    assert m.search_hit_at_k == {1: 0.0, 5: 1.0}
    assert m.diagnosis_hit_at_k == {1: 1.0, 5: 1.0}
    assert m.citation_validity_rate == 0.5
    assert m.root_cause_alignment == "correct"
    assert m.issue_echo_keyword_overlap > 0.0


def test_compute_evaluation_metrics_failed_run() -> None:
    m = compute_evaluation_metrics(_case(["a.py"]), None, error_reason="boom", k_values=[5])
    assert m.status == "failed"
    assert m.root_cause_alignment == "failed"
    assert m.error_reason == "boom"


def _metric(
    case_id: str, status: str, alignment: str, rate: float | None, unsupported: int
) -> CaseEvaluationMetrics:
    return CaseEvaluationMetrics(
        case_id=case_id,
        repo_full_name="sample/repo",
        issue_number=1,
        target_commit_sha=SAMPLE_SHA,
        status=status,
        citation_validity_rate=rate,
        search_recall_at_k={5: 1.0},
        search_hit_at_k={5: 1.0},
        diagnosis_hit_at_k={5: 1.0},
        unsupported_claims_count=unsupported,
        root_cause_alignment=alignment,
    )


def test_aggregate_excludes_failed_runs_from_quality_means() -> None:
    """Regression: failed runs were averaged in as 0% citation validity (85% headline)."""
    agg = aggregate_evaluation_metrics(
        [
            _metric("A", "completed", "correct", 1.0, 0),
            _metric("B", "insufficient_evidence", "insufficient_evidence", None, 2),
            _metric("C", "failed", "failed", None, 0),
        ],
        k_values=[5],
    )
    assert agg.total_cases == 3
    assert agg.scored_cases == 2
    assert agg.failed_cases == 1
    assert agg.completion_rate == round(2 / 3, 4)
    assert agg.cases_with_citations == 1
    assert agg.mean_citation_validity == 1.0
    assert agg.mean_unsupported_claims == 1.0
    assert agg.root_cause_useful_rate == 0.5


def test_aggregate_checks_all_five_documented_targets() -> None:
    """Regression: only 3 of the 5 targets in evals/README.md were computed."""
    agg = aggregate_evaluation_metrics(
        [_metric("A", "completed", "correct", 1.0, 0)], k_values=[5]
    )
    assert set(agg.benchmark_targets_met) == {
        "citation_validity_ge_95",
        "search_recall_at_5_ge_80",
        "search_hit_at_5_ge_90",
        "unsupported_claims_lt_0_5",
        "root_cause_useful_ge_70",
    }
    assert all(agg.benchmark_targets_met.values())


def test_aggregate_with_no_citations_does_not_pass_citation_target() -> None:
    agg = aggregate_evaluation_metrics(
        [_metric("A", "insufficient_evidence", "insufficient_evidence", None, 1)], k_values=[5]
    )
    assert agg.mean_citation_validity is None
    assert agg.benchmark_targets_met["citation_validity_ge_95"] is False
