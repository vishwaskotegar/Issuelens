"""Unit tests for deterministic citation validation and insufficient-evidence guardrails."""

from pathlib import Path

from issuelens.agent.report import (
    EvidenceItem,
    Hypothesis,
    InvestigationReport,
)
from issuelens.agent.validator import (
    validate_citation,
    validate_investigation_report,
)
from issuelens.repository.snapshot import RepoSnapshot
from issuelens.repository.snippets import read_snippet

FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "sample_repo"
SAMPLE_COMMIT = "1122334455667788990011223344556677889900"


def get_fixture_snapshot() -> RepoSnapshot:
    """Helper providing fixture RepoSnapshot for citation verification."""
    return RepoSnapshot(
        root_path=FIXTURES_DIR,
        repo_full_name="sample/repo",
        commit_sha=SAMPLE_COMMIT,
    )


def test_validate_citation_valid_code() -> None:
    """Verify physical source citation matching snapshot passes all checks."""
    snapshot = get_fixture_snapshot()
    real_snippet = read_snippet(snapshot, "sample_pkg/auth.py", 1, 5)

    item = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        excerpt=real_snippet,
        commit_sha=SAMPLE_COMMIT,
    )

    result = validate_citation(item, snapshot)
    assert result.is_valid is True
    assert result.file_exists is True
    assert result.lines_valid is True
    assert result.excerpt_matches is True
    assert result.commit_matches is True
    assert result.error_reason is None


def test_validate_citation_nonexistent_file() -> None:
    """Verify citation referencing nonexistent file fails validation."""
    snapshot = get_fixture_snapshot()
    item = EvidenceItem(
        evidence_id="ev_missing",
        source_type="code",
        path_or_url="sample_pkg/nonexistent.py",
        start_line=1,
        end_line=10,
        excerpt="some code",
        commit_sha=SAMPLE_COMMIT,
    )

    result = validate_citation(item, snapshot)
    assert result.is_valid is False
    assert result.file_exists is False
    assert "does not exist in snapshot" in str(result.error_reason)


def test_validate_citation_path_traversal() -> None:
    """Verify path traversal in citation is caught by safety boundary."""
    snapshot = get_fixture_snapshot()
    item = EvidenceItem(
        evidence_id="ev_traversal",
        source_type="code",
        path_or_url="../../etc/passwd",
        start_line=1,
        end_line=5,
        excerpt="root:x:0:0",
        commit_sha=SAMPLE_COMMIT,
    )

    result = validate_citation(item, snapshot)
    assert result.is_valid is False
    assert "Path safety violation" in str(result.error_reason)


def test_validate_citation_out_of_bounds_lines() -> None:
    """Verify line numbers exceeding physical file length fail validation."""
    snapshot = get_fixture_snapshot()
    item = EvidenceItem(
        evidence_id="ev_oob",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=900,
        end_line=950,
        excerpt="fabricated code",
        commit_sha=SAMPLE_COMMIT,
    )

    result = validate_citation(item, snapshot)
    assert result.is_valid is False
    assert result.file_exists is True
    assert result.lines_valid is False
    assert "exceeds total lines" in str(result.error_reason)


def test_validate_citation_inverted_lines() -> None:
    """Verify inverted start/end lines fail validation."""
    snapshot = get_fixture_snapshot()
    item = EvidenceItem(
        evidence_id="ev_inv",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=20,
        end_line=10,
        excerpt="code",
        commit_sha=SAMPLE_COMMIT,
    )

    result = validate_citation(item, snapshot)
    assert result.is_valid is False
    assert result.lines_valid is False
    assert "Invalid line coordinates" in str(result.error_reason)


def test_validate_citation_missing_line_numbers() -> None:
    """Verify code citation without line numbers fails validation."""
    snapshot = get_fixture_snapshot()
    item = EvidenceItem(
        evidence_id="ev_nolines",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=None,
        end_line=None,
        excerpt="code",
        commit_sha=SAMPLE_COMMIT,
    )

    result = validate_citation(item, snapshot)
    assert result.is_valid is False
    assert result.lines_valid is False
    assert "must specify both start_line and end_line" in str(result.error_reason)


def test_validate_citation_commit_mismatch() -> None:
    """Verify commit SHA mismatch fails validation."""
    snapshot = get_fixture_snapshot()
    item = EvidenceItem(
        evidence_id="ev_wrong_commit",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        excerpt="code",
        commit_sha="0000000000000000000000000000000000000000",
    )

    result = validate_citation(item, snapshot)
    assert result.is_valid is False
    assert result.commit_matches is False
    assert "Commit SHA mismatch" in str(result.error_reason)


def test_validate_citation_altered_excerpt() -> None:
    """Verify fabricated or altered excerpt text fails validation."""
    snapshot = get_fixture_snapshot()
    item = EvidenceItem(
        evidence_id="ev_fake_text",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        excerpt="1: def completely_fabricated_function():\n2:     return None",
        commit_sha=SAMPLE_COMMIT,
    )

    result = validate_citation(item, snapshot)
    assert result.is_valid is False
    assert result.excerpt_matches is False
    assert "Excerpt content does not match" in str(result.error_reason)


def test_validate_investigation_report_fully_verified() -> None:
    """Verify report with valid citations receives 'verified' status."""
    snapshot = get_fixture_snapshot()
    snippet = read_snippet(snapshot, "sample_pkg/auth.py", 1, 5)

    item = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        excerpt=snippet,
        commit_sha=SAMPLE_COMMIT,
    )
    hyp = Hypothesis(
        title="Valid hypothesis",
        explanation="Explanation",
        confidence="high",
        supporting_evidence_ids=["ev_1"],
    )
    report = InvestigationReport(
        run_id="inv_test",
        issue_url="https://github.com/sample/repo/issues/1",
        repository="sample/repo",
        commit_sha=SAMPLE_COMMIT,
        summary="Analysis complete.",
        hypotheses=[hyp],
        evidence=[item],
        verification_status="not_run",
    )

    sanitized_report, val_res = validate_investigation_report(report, snapshot)
    assert val_res.is_valid is True
    assert val_res.verification_status == "verified"
    assert sanitized_report.verification_status == "verified"
    assert val_res.insufficient_evidence is False
    assert len(sanitized_report.evidence) == 1
    assert sanitized_report.hypotheses[0].supporting_evidence_ids == ["ev_1"]


def test_validate_investigation_report_strips_hallucinated_citations() -> None:
    """Verify hallucinated citations are stripped and ungrounded hypotheses demoted."""
    snapshot = get_fixture_snapshot()
    valid_snippet = read_snippet(snapshot, "sample_pkg/auth.py", 1, 5)

    valid_item = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        excerpt=valid_snippet,
        commit_sha=SAMPLE_COMMIT,
    )
    bad_item = EvidenceItem(
        evidence_id="ev_bad",
        source_type="code",
        path_or_url="sample_pkg/ghost.py",
        start_line=1,
        end_line=5,
        excerpt="ghost code",
        commit_sha=SAMPLE_COMMIT,
    )
    hyp_ungrounded = Hypothesis(
        title="Ungrounded hypothesis citing bad evidence",
        explanation="Explanation",
        confidence="high",
        supporting_evidence_ids=["ev_bad", "ev_nonexistent_99"],
    )
    hyp_partial = Hypothesis(
        title="Partially grounded hypothesis",
        explanation="Explanation",
        confidence="high",
        supporting_evidence_ids=["ev_1", "ev_bad"],
    )

    report = InvestigationReport(
        run_id="inv_test",
        issue_url="https://github.com/sample/repo/issues/1",
        repository="sample/repo",
        commit_sha=SAMPLE_COMMIT,
        summary="Analysis with mixed citations.",
        hypotheses=[hyp_ungrounded, hyp_partial],
        evidence=[valid_item, bad_item],
        verification_status="not_run",
    )

    sanitized_report, val_res = validate_investigation_report(report, snapshot)
    assert val_res.is_valid is False
    assert val_res.verification_status == "partially_verified"
    assert sanitized_report.verification_status == "partially_verified"

    # Invalid evidence item removed from final report so it cannot appear as verified
    assert len(sanitized_report.evidence) == 1
    assert sanitized_report.evidence[0].evidence_id == "ev_1"

    # Fully ungrounded hypothesis demoted to low confidence
    assert sanitized_report.hypotheses[0].supporting_evidence_ids == []
    assert sanitized_report.hypotheses[0].confidence == "low"
    assert (
        "Citation Guardrail: Cited evidence"
        in sanitized_report.hypotheses[0].confidence_rationale
    )

    # Partially grounded hypothesis retains valid citations only
    assert sanitized_report.hypotheses[1].supporting_evidence_ids == ["ev_1"]
    assert (
        "Citation Guardrail: Partial citations"
        in sanitized_report.hypotheses[1].confidence_rationale
    )


def test_validate_investigation_report_insufficient_evidence_fallback() -> None:
    """Verify empty/zero verified evidence triggers insufficient-evidence behavior."""
    snapshot = get_fixture_snapshot()
    report = InvestigationReport(
        run_id="inv_empty",
        issue_url="https://github.com/sample/repo/issues/1",
        repository="sample/repo",
        commit_sha=SAMPLE_COMMIT,
        summary="No evidence could be gathered.",
        hypotheses=[],
        evidence=[],
        verification_status="not_run",
    )

    sanitized_report, val_res = validate_investigation_report(report, snapshot)
    assert val_res.insufficient_evidence is True
    assert "[INSUFFICIENT EVIDENCE]" in sanitized_report.summary
    assert any(
        "No verified source code evidence" in info
        for info in sanitized_report.missing_information
    )
    assert any("Citation Guardrail:" in lim for lim in sanitized_report.limitations)
    assert sanitized_report.verification_status == "not_run"
