"""Unit tests for InvestigationReport, Hypothesis, and EvidenceItem schemas."""

import pytest
from pydantic import ValidationError

from issuelens.agent.report import (
    EvidenceItem,
    Hypothesis,
    InvestigationReport,
    InvestigationReportPayload,
)


def test_evidence_item_valid() -> None:
    """Verify EvidenceItem instantiates and serializes correctly."""
    item = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="src/auth.py",
        start_line=10,
        end_line=25,
        excerpt="10: def verify():\n11:     pass",
        commit_sha="abcdef1234567890abcdef1234567890abcdef12",
    )
    assert item.evidence_id == "ev_1"
    assert item.source_type == "code"
    assert item.start_line == 10
    assert item.end_line == 25
    assert item.commit_sha == "abcdef1234567890abcdef1234567890abcdef12"

    dumped = item.model_dump()
    assert dumped["evidence_id"] == "ev_1"
    assert dumped["source_type"] == "code"


def test_evidence_item_invalid_line_numbers() -> None:
    """Verify start_line and end_line reject non-positive values."""
    with pytest.raises(ValidationError):
        EvidenceItem(
            evidence_id="ev_1",
            source_type="code",
            path_or_url="src/auth.py",
            start_line=0,
            excerpt="code",
        )


def test_hypothesis_valid() -> None:
    """Verify Hypothesis instantiates with qualitative confidence."""
    hyp = Hypothesis(
        title="Parameter unescaping bug",
        explanation="Query string is not escaped before HTTP dispatch.",
        confidence="high",
        confidence_rationale="Traceback points directly to prepare_url in requests/models.py",
        supporting_evidence_ids=["ev_1"],
        contradicting_evidence_ids=[],
        verification_steps=["Run test with special chars"],
    )
    assert hyp.title == "Parameter unescaping bug"
    assert hyp.confidence == "high"
    assert hyp.supporting_evidence_ids == ["ev_1"]


def test_hypothesis_invalid_confidence() -> None:
    """Verify invalid confidence rating fails validation."""
    with pytest.raises(ValidationError):
        Hypothesis(
            title="Test",
            explanation="Explanation",
            confidence="very_high",  # type: ignore[arg-type]
        )


def test_investigation_report_valid_full() -> None:
    """Verify complete InvestigationReport validation."""
    evidence = [
        EvidenceItem(
            evidence_id="ev_1",
            source_type="code",
            path_or_url="requests/models.py",
            start_line=100,
            end_line=110,
            excerpt="100: def prepare_url(): ...",
            commit_sha="1111222233334444555566667777888899990000",
        )
    ]
    hypotheses = [
        Hypothesis(
            title="Unquoted query params",
            explanation="Values are concatenated without quote_plus.",
            confidence="medium",
            confidence_rationale="Matches reported URL failure.",
            supporting_evidence_ids=["ev_1"],
            verification_steps=["Inspect query param formatting"],
        )
    ]

    report = InvestigationReport(
        run_id="inv_test1234",
        issue_url="https://github.com/psf/requests/issues/1234",
        repository="psf/requests",
        commit_sha="1111222233334444555566667777888899990000",
        summary="Investigation into query parameter formatting bug.",
        observed_facts=["User reported ValueError with space in URL"],
        hypotheses=hypotheses,
        evidence=evidence,
        missing_information=["Exact python runtime environment"],
        suggested_tests=["test_query_with_space"],
        recommended_next_steps=["Sanitize query dict in prepare_url"],
        verification_status="not_run",
        limitations=["Static analysis without dynamic code execution"],
    )

    assert report.run_id == "inv_test1234"
    assert report.verification_status == "not_run"
    assert len(report.hypotheses) == 1
    assert len(report.evidence) == 1
    assert report.hypotheses[0].supporting_evidence_ids == ["ev_1"]


def test_investigation_report_invalid_verification_status() -> None:
    """Verify invalid verification_status fails validation."""
    with pytest.raises(ValidationError):
        InvestigationReport(
            run_id="inv_123",
            issue_url="https://github.com/owner/repo/issues/1",
            repository="owner/repo",
            commit_sha="abcdef1234567890abcdef1234567890abcdef12",
            summary="Summary",
            verification_status="passed",  # type: ignore[arg-type]
        )


def test_investigation_report_from_payload() -> None:
    """Verify factory from_payload merges LLM payload with verified provenance."""
    payload = InvestigationReportPayload(
        summary="Analysis complete.",
        observed_facts=["Observed fact 1"],
        hypotheses=[
            Hypothesis(
                title="Root cause hypothesis",
                explanation="Detailed explanation",
                confidence="high",
                confidence_rationale="Grounded in ev_1",
                supporting_evidence_ids=["ev_1"],
            )
        ],
        missing_information=["None"],
        suggested_tests=["test_something"],
        recommended_next_steps=["Fix it"],
        limitations=["Static only"],
    )
    evidence = [
        EvidenceItem(
            evidence_id="ev_1",
            source_type="code",
            path_or_url="pkg/mod.py",
            start_line=1,
            end_line=5,
            excerpt="1: def f(): pass",
            commit_sha="abcdef1234567890abcdef1234567890abcdef12",
        )
    ]
    report = InvestigationReport.from_payload(
        payload=payload,
        run_id="inv_abc123",
        issue_url="https://github.com/owner/repo/issues/1",
        repository="owner/repo",
        commit_sha="abcdef1234567890abcdef1234567890abcdef12",
        evidence=evidence,
    )
    assert report.run_id == "inv_abc123"
    assert report.verification_status == "not_run"
    assert report.summary == "Analysis complete."
    assert report.evidence[0].evidence_id == "ev_1"
    assert report.hypotheses[0].confidence == "high"


