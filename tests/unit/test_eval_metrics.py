"""Unit tests for evaluation metrics engine."""

from pathlib import Path

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
    compute_citation_validity,
    compute_evaluation_metrics,
    compute_recall_at_k,
    compute_unsupported_claims,
    score_root_cause_alignment,
)
from issuelens.repository.snapshot import RepoSnapshot
from issuelens.repository.snippets import read_snippet

SAMPLE_SHA = "0123456789abcdef0123456789abcdef01234567"
SAMPLE_REPO_DIR = Path("tests/fixtures/sample_repo").resolve()


def _make_report(
    evidence: list[EvidenceItem],
    hypotheses: list[Hypothesis],
    verification_status: VerificationStatus = "verified",
) -> InvestigationReport:
    return InvestigationReport(
        run_id="run_test_001",
        issue_url="https://github.com/sample/repo/issues/100",
        repository="sample/repo",
        commit_sha=SAMPLE_SHA,
        summary="Test report summary.",
        observed_facts=["Observed behavior A."],
        hypotheses=hypotheses,
        evidence=evidence,
        missing_information=[],
        suggested_tests=["tests/test_something.py"],
        recommended_next_steps=["Inspect token validation"],
        verification_status=verification_status,
        limitations=[],
    )


def test_compute_citation_validity_empty() -> None:
    """Empty evidence returns 0 citations and 1.0 validity."""
    report = _make_report([], [])
    total, valid, rate, errors = compute_citation_validity(report)
    assert total == 0
    assert valid == 0
    assert rate == 1.0
    assert errors == []


def test_compute_citation_validity_without_snapshot() -> None:
    """Without snapshot, checks structural presence of coordinates."""
    ev1 = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        commit_sha=SAMPLE_SHA,
        excerpt="def authenticate(): pass",
    )
    ev2 = EvidenceItem(
        evidence_id="ev_2",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=None,
        end_line=None,
        commit_sha=SAMPLE_SHA,
        excerpt="missing coordinates",
    )
    report = _make_report([ev1, ev2], [])
    total, valid, rate, errors = compute_citation_validity(report, snapshot=None)
    assert total == 2
    assert valid == 1
    assert rate == 0.5
    assert len(errors) == 1


def test_compute_citation_validity_with_snapshot() -> None:
    """With physical snapshot, verifies real byte excerpt match."""
    snapshot = RepoSnapshot(
        root_path=SAMPLE_REPO_DIR,
        repo_full_name="sample/repo",
        commit_sha=SAMPLE_SHA,
    )
    snippet = read_snippet(snapshot, "sample_pkg/auth.py", 1, 5)

    ev_valid = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        commit_sha=SAMPLE_SHA,
        excerpt=snippet,
    )
    ev_invalid_path = EvidenceItem(
        evidence_id="ev_2",
        source_type="code",
        path_or_url="sample_pkg/nonexistent.py",
        start_line=1,
        end_line=2,
        commit_sha=SAMPLE_SHA,
        excerpt="something",
    )
    report = _make_report([ev_valid, ev_invalid_path], [])

    total, valid, rate, errors = compute_citation_validity(report, snapshot)
    assert total == 2
    assert valid == 1
    assert rate == 0.5
    assert len(errors) == 1
    assert "nonexistent.py" in errors[0]


def test_compute_recall_and_hit_at_k() -> None:
    """Test recall@k and hit@k across different ranks and normalization."""
    retrieved = [
        "src\\flask\\helpers.py",
        "src/flask/app.py",
        "src/flask/blueprints.py",
        "src/flask/views.py",
    ]
    expected = ["src/flask/blueprints.py"]

    recall, hit = compute_recall_at_k(retrieved, expected, k_values=[1, 2, 3, 5])
    # Position of blueprints.py is 3 (1-indexed)
    assert recall[1] == 0.0
    assert hit[1] == 0.0
    assert recall[2] == 0.0
    assert hit[2] == 0.0
    assert recall[3] == 1.0
    assert hit[3] == 1.0
    assert recall[5] == 1.0
    assert hit[5] == 1.0

    # Test multiple expected files
    multi_expected = ["src/flask/app.py", "src/flask/blueprints.py", "src/flask/missing.py"]
    recall_m, hit_m = compute_recall_at_k(retrieved, multi_expected, k_values=[1, 2, 3, 5])
    assert recall_m[1] == 0.0
    assert round(recall_m[2], 2) == 0.33
    assert round(recall_m[3], 2) == 0.67
    assert hit_m[2] == 1.0

    # Test empty expected files defaults to 1.0
    recall_empty, hit_empty = compute_recall_at_k(retrieved, [], k_values=[1, 3])
    assert recall_empty[1] == 1.0
    assert hit_empty[1] == 1.0


def test_compute_unsupported_claims() -> None:
    """Test detection of ungrounded hypotheses and non-existent evidence IDs."""
    ev1 = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=2,
        commit_sha=SAMPLE_SHA,
        excerpt="import os",
    )
    # Grounded hypothesis
    hyp1 = Hypothesis(
        title="Valid hypothesis",
        explanation="Explanation backed by ev_1.",
        confidence="high",
        supporting_evidence_ids=["ev_1"],
    )
    # Ungrounded hypothesis (no evidence IDs)
    hyp2 = Hypothesis(
        title="Ungrounded hypothesis",
        explanation="No evidence provided.",
        confidence="low",
        supporting_evidence_ids=[],
    )
    # Dangling reference hypothesis
    hyp3 = Hypothesis(
        title="Dangling reference hypothesis",
        explanation="References non-existent ev_99.",
        confidence="medium",
        supporting_evidence_ids=["ev_99"],
    )
    report = _make_report([ev1], [hyp1, hyp2, hyp3])

    count, reasons = compute_unsupported_claims(report)
    assert count == 2
    assert len(reasons) == 2
    assert "no supporting evidence IDs" in reasons[0]
    assert "ev_99" in reasons[1]


def test_score_root_cause_alignment() -> None:
    """Test alignment scoring against ground truth known resolution."""
    hyp_correct = Hypothesis(
        title="URL prefix blueprint routing issue",
        explanation="The blueprint url_prefix was not applied when nesting sub-blueprints.",
        confidence="high",
        supporting_evidence_ids=["ev_1"],
    )
    report_correct = _make_report([], [hyp_correct])
    known_resolution = "Fixed url prefix handling when blueprints are nested."

    category, score = score_root_cause_alignment(report_correct, known_resolution)
    assert category in ("correct", "partially_useful")
    assert score > 0.3

    # Completely off-topic hypothesis
    hyp_wrong = Hypothesis(
        title="Database thread pool exhaustion",
        explanation="Postgres connection timeout caused by unclosed cursor transactions.",
        confidence="low",
        supporting_evidence_ids=["ev_1"],
    )
    report_wrong = _make_report([], [hyp_wrong])
    cat_wrong, score_wrong = score_root_cause_alignment(report_wrong, known_resolution)
    assert cat_wrong == "incorrect"
    assert score_wrong < 0.15


def test_compute_evaluation_metrics_full() -> None:
    """Test end-to-end single case metrics computation."""
    case = EvaluationCaseModel(
        id="CASE-001",
        issue_url="https://github.com/sample/repo/issues/100",
        repo_full_name="sample/repo",
        issue_number=100,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="Fixed authentication token expiration logic in auth.py.",
        expected_files=["sample_pkg/auth.py"],
    )

    snapshot = RepoSnapshot(
        root_path=SAMPLE_REPO_DIR,
        repo_full_name="sample/repo",
        commit_sha=SAMPLE_SHA,
    )
    snippet = read_snippet(snapshot, "sample_pkg/auth.py", 1, 5)

    ev = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        commit_sha=SAMPLE_SHA,
        excerpt=snippet,
    )
    hyp = Hypothesis(
        title="Token expiration bug in auth",
        explanation="Authentication token expiration logic fails in auth.py.",
        confidence="high",
        supporting_evidence_ids=["ev_1"],
    )
    report = _make_report([ev], [hyp])

    metrics = compute_evaluation_metrics(
        case=case,
        report=report,
        snapshot=snapshot,
        latency_seconds=1.234,
        k_values=[1, 3, 5],
    )

    assert metrics.case_id == "CASE-001"
    assert metrics.status == "completed"
    assert metrics.citation_validity_rate == 1.0
    assert metrics.recall_at_k[1] == 1.0
    assert metrics.hit_at_k[1] == 1.0
    assert metrics.unsupported_claims_count == 0
    assert metrics.root_cause_alignment in ("correct", "partially_useful")
    assert metrics.latency_seconds == 1.234


def test_compute_evaluation_metrics_failed_run() -> None:
    """Test metrics computation when report generation failed."""
    case = EvaluationCaseModel(
        id="CASE-002",
        issue_url="https://github.com/sample/repo/issues/102",
        repo_full_name="sample/repo",
        issue_number=102,
        target_commit_sha=SAMPLE_SHA,
        known_resolution="Fixed something.",
        expected_files=["sample_pkg/auth.py"],
    )

    metrics = compute_evaluation_metrics(
        case=case,
        report=None,
        latency_seconds=0.5,
        error_reason="Connection timeout",
    )

    assert metrics.status == "failed"
    assert metrics.citation_validity_rate == 0.0
    assert metrics.recall_at_k[1] == 0.0
    assert metrics.error_reason == "Connection timeout"


def test_aggregate_evaluation_metrics() -> None:
    """Test aggregation of multiple CaseEvaluationMetrics."""
    # Test empty list
    empty_agg = aggregate_evaluation_metrics([])
    assert empty_agg.total_cases == 0
    assert empty_agg.completed_cases == 0

    m1 = CaseEvaluationMetrics(
        case_id="CASE-001",
        repo_full_name="a/b",
        issue_number=1,
        target_commit_sha=SAMPLE_SHA,
        status="completed",
        total_code_citations=2,
        valid_code_citations=2,
        citation_validity_rate=1.0,
        recall_at_k={1: 1.0, 5: 1.0},
        hit_at_k={1: 1.0, 5: 1.0},
        unsupported_claims_count=0,
        root_cause_alignment="correct",
        latency_seconds=1.0,
    )
    m2 = CaseEvaluationMetrics(
        case_id="CASE-002",
        repo_full_name="a/b",
        issue_number=2,
        target_commit_sha=SAMPLE_SHA,
        status="completed",
        total_code_citations=2,
        valid_code_citations=1,
        citation_validity_rate=0.5,
        recall_at_k={1: 0.0, 5: 1.0},
        hit_at_k={1: 0.0, 5: 1.0},
        unsupported_claims_count=1,
        root_cause_alignment="partially_useful",
        latency_seconds=3.0,
    )

    agg = aggregate_evaluation_metrics([m1, m2], k_values=[1, 5])
    assert agg.total_cases == 2
    assert agg.completed_cases == 2
    assert agg.failed_cases == 0
    assert agg.mean_citation_validity == 0.75
    assert agg.mean_recall_at_k[1] == 0.5
    assert agg.mean_recall_at_k[5] == 1.0
    assert agg.mean_unsupported_claims == 0.5
    assert agg.mean_latency_seconds == 2.0
    assert agg.root_cause_distribution["correct"] == 1
    assert agg.root_cause_distribution["partially_useful"] == 1
    assert len(agg.failure_notes) == 1  # m2 has citation validity < 0.95
