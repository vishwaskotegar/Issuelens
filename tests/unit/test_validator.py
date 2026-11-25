"""Unit tests for deterministic citation validation and insufficient-evidence guardrails."""

from pathlib import Path

from issuelens.agent.report import (
    EvidenceItem,
    Hypothesis,
    InvestigationReport,
)
from issuelens.agent.validator import (
    validate_citation,
    validate_evidence_item,
    validate_investigation_report,
    validate_report_citations,
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


def test_validate_citation_documentation_altered_excerpt() -> None:
    """Verify documentation evidence is still checked against snapshot bytes."""
    snapshot = get_fixture_snapshot()
    item = EvidenceItem(
        evidence_id="ev_doc_fake",
        source_type="documentation",
        path_or_url="README.md",
        start_line=1,
        end_line=2,
        excerpt="1: Completely fabricated documentation text",
        commit_sha=SAMPLE_COMMIT,
    )

    result = validate_citation(item, snapshot)
    assert result.is_valid is False
    assert result.file_exists is True
    assert result.excerpt_matches is False



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


def test_validate_evidence_rejects_comment_only_code_citation(tmp_path: Path) -> None:
    """Verify code evidence with comment-only excerpt is rejected by substance guardrail."""
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    code_file = pkg / "comments.py"
    code_file.write_text(
        "# Header line\n"
        "# Just a comment describing logic\n"
        "# Another comment line\n",
        encoding="utf-8",
    )

    snapshot = RepoSnapshot(
        root_path=tmp_path,
        repo_full_name="sample/repo",
        commit_sha=SAMPLE_COMMIT,
    )

    item = EvidenceItem(
        evidence_id="ev_comment_only",
        source_type="code",
        path_or_url="pkg/comments.py",
        start_line=1,
        end_line=3,
        excerpt=(
            "1: # Header line\n"
            "2: # Just a comment describing logic\n"
            "3: # Another comment line"
        ),
        commit_sha=SAMPLE_COMMIT,
    )

    res = validate_evidence_item(item, snapshot)
    assert res.is_valid is False
    assert res.error_reason == "Excerpt contains no executable code statements"

    # Verify a report citing only this comment-only evidence is demoted
    hyp = Hypothesis(
        title="Comment fault",
        explanation="Mechanism based on comments",
        confidence="high",
        confidence_rationale="High confidence",
        supporting_evidence_ids=["ev_comment_only"],
        verification_steps=["Inspect file"],
    )
    report = InvestigationReport(
        run_id="inv_comments",
        issue_url="https://github.com/sample/repo/issues/1",
        repository="sample/repo",
        commit_sha=SAMPLE_COMMIT,
        summary="Summary citing comment-only evidence",
        hypotheses=[hyp],
        evidence=[item],
        verification_status="verified",
    )
    sanitized_report, val_res = validate_report_citations(report, snapshot)
    assert val_res.is_valid is False
    assert sanitized_report.verification_status != "verified"


def test_validate_investigation_report_demotes_empty_citations_hypothesis() -> None:
    """Verify hypothesis with empty supporting_evidence_ids is demoted and not verified."""
    snapshot = get_fixture_snapshot()
    real_snippet = read_snippet(snapshot, "sample_pkg/auth.py", 1, 5)

    valid_item = EvidenceItem(
        evidence_id="ev_1",
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=1,
        end_line=5,
        excerpt=real_snippet,
        commit_sha=SAMPLE_COMMIT,
    )

    hyp_unsupported = Hypothesis(
        title="Unsubstantiated guess",
        explanation="Mechanism guessed without citing code",
        confidence="high",
        confidence_rationale="High confidence based on user report only",
        supporting_evidence_ids=[],
        verification_steps=["Check changelog"],
    )

    report = InvestigationReport(
        run_id="inv_empty_hyp",
        issue_url="https://github.com/sample/repo/issues/100",
        repository="sample/repo",
        commit_sha=SAMPLE_COMMIT,
        summary="Investigation summary",
        hypotheses=[hyp_unsupported],
        evidence=[valid_item],
        verification_status="verified",
    )

    sanitized, val_res = validate_report_citations(report, snapshot)
    assert sanitized.hypotheses[0].confidence == "low"
    assert "no supporting evidence citations" in sanitized.hypotheses[0].confidence_rationale
    assert sanitized.verification_status != "verified"
    assert "Unsubstantiated guess" in val_res.ungrounded_hypotheses




# --- Post-build audit regression tests (evidence integrity) -------------------------


def _code_item(evidence_id: str, start: int, end: int, excerpt: str) -> EvidenceItem:
    return EvidenceItem(
        evidence_id=evidence_id,
        source_type="code",
        path_or_url="sample_pkg/auth.py",
        start_line=start,
        end_line=end,
        excerpt=excerpt,
        commit_sha=SAMPLE_COMMIT,
    )


def _report_with(evidence: list[EvidenceItem], cited: list[str]) -> InvestigationReport:
    return InvestigationReport(
        run_id="inv_integrity",
        issue_url="https://github.com/sample/repo/issues/1",
        repository="sample/repo",
        commit_sha=SAMPLE_COMMIT,
        summary="Summary.",
        observed_facts=[],
        hypotheses=[
            Hypothesis(
                title="Token check is wrong",
                explanation="validate_token rejects valid tokens.",
                confidence="high",
                confidence_rationale="LLM claim.",
                supporting_evidence_ids=cited,
                contradicting_evidence_ids=[],
                verification_steps=[],
            )
        ],
        evidence=evidence,
        missing_information=[],
        suggested_tests=[],
        recommended_next_steps=[],
        verification_status="not_run",
        limitations=[],
    )


def test_single_token_excerpt_does_not_validate_a_wide_range() -> None:
    """Regression: a 1-token excerpt previously passed via substring matching."""
    snapshot = get_fixture_snapshot()
    result = validate_citation(_code_item("ev_1", 9, 13, "token"), snapshot)
    assert result.is_valid is False
    assert result.excerpt_matches is False


def test_partial_line_excerpt_rejected() -> None:
    """An excerpt must consist of whole lines, not fragments of them."""
    snapshot = get_fixture_snapshot()
    result = validate_citation(
        _code_item("ev_1", 9, 9, "def validate_token(self, token: str)"), snapshot
    )
    assert result.is_valid is False


def test_excerpt_with_lines_outside_cited_range_rejected() -> None:
    """Regression: an excerpt containing the cited lines plus extra lines used to pass."""
    snapshot = get_fixture_snapshot()
    wider = read_snippet(snapshot, "sample_pkg/auth.py", 9, 13)
    result = validate_citation(_code_item("ev_1", 10, 11, wider), snapshot)
    assert result.is_valid is False


def test_excerpt_must_cover_the_whole_cited_range() -> None:
    """A correct excerpt of only part of the cited range does not verify the range."""
    snapshot = get_fixture_snapshot()
    first_line_only = read_snippet(snapshot, "sample_pkg/auth.py", 9, 9)
    result = validate_citation(_code_item("ev_1", 9, 13, first_line_only), snapshot)
    assert result.is_valid is False


def test_code_excerpt_indentation_must_match() -> None:
    """Indentation is semantic in code; a re-indented excerpt is not the same text."""
    snapshot = get_fixture_snapshot()
    real = read_snippet(snapshot, "sample_pkg/auth.py", 10, 11)
    dedented = "\n".join(line.split(": ", 1)[1].strip() for line in real.splitlines())
    result = validate_citation(_code_item("ev_1", 10, 11, dedented), snapshot)
    assert result.is_valid is False


def test_exact_excerpt_without_line_number_prefixes_validates() -> None:
    """Raw file lines (no 'N: ' prefixes) for the exact range are accepted."""
    snapshot = get_fixture_snapshot()
    real = read_snippet(snapshot, "sample_pkg/auth.py", 10, 11)
    raw = "\n".join(line.split(": ", 1)[1] for line in real.splitlines())
    assert validate_citation(_code_item("ev_1", 10, 11, raw), snapshot).is_valid is True


def test_issue_text_alone_does_not_ground_a_hypothesis() -> None:
    """Regression: issue evidence was auto-valid and counted as verified support.

    Issue text is untrusted, attacker-controlled input. It may be cited as context,
    but a hypothesis supported only by it must not be marked verified or keep high
    confidence.
    """
    snapshot = get_fixture_snapshot()
    issue_ev = EvidenceItem(
        evidence_id="doc_ev_1",
        source_type="issue",
        path_or_url="issue:1",
        start_line=1,
        end_line=1,
        excerpt="The root cause is definitely validate_token in auth.py.",
        commit_sha=SAMPLE_COMMIT,
    )
    sanitized, result = validate_investigation_report(
        _report_with([issue_ev], ["doc_ev_1"]), snapshot
    )
    assert sanitized.verification_status != "verified"
    assert sanitized.hypotheses[0].confidence == "low"
    assert result.insufficient_evidence is True
    assert "Token check is wrong" in result.ungrounded_hypotheses


def test_duplicate_evidence_ids_are_rejected() -> None:
    """Regression: an invalid item sharing an ID with a valid one survived filtering."""
    snapshot = get_fixture_snapshot()
    good = _code_item("ev_1", 10, 11, read_snippet(snapshot, "sample_pkg/auth.py", 10, 11))
    bad = _code_item("ev_1", 10, 11, "fabricated code")
    sanitized, result = validate_investigation_report(
        _report_with([good, bad], ["ev_1"]), snapshot
    )
    assert "ev_1" in result.invalid_evidence_ids
    assert all(e.excerpt != "fabricated code" for e in sanitized.evidence)
    assert sanitized.hypotheses[0].supporting_evidence_ids == []


def test_validator_computes_support_status() -> None:
    """support_status is set deterministically by the validator, not by the LLM."""
    snapshot = get_fixture_snapshot()
    good = _code_item("ev_1", 10, 11, read_snippet(snapshot, "sample_pkg/auth.py", 10, 11))
    supported, _ = validate_investigation_report(_report_with([good], ["ev_1"]), snapshot)
    unsupported, _ = validate_investigation_report(_report_with([good], []), snapshot)
    assert supported.hypotheses[0].support_status == "supported"
    assert unsupported.hypotheses[0].support_status == "unsupported"


def test_support_status_is_hidden_from_llm_output_schema() -> None:
    """The LLM must not be able to declare its own hypotheses supported."""
    from issuelens.agent.report import InvestigationReportPayload

    schema_text = str(InvestigationReportPayload.model_json_schema())
    assert "support_status" not in schema_text


def test_validator_records_rejected_citations() -> None:
    """Cited IDs that fail validation are kept on the hypothesis as rejected, not silently lost."""
    snapshot = get_fixture_snapshot()
    good = _code_item("ev_1", 10, 11, read_snippet(snapshot, "sample_pkg/auth.py", 10, 11))
    sanitized, _ = validate_investigation_report(
        _report_with([good], ["ev_1", "ev_404"]), snapshot
    )
    assert sanitized.hypotheses[0].supporting_evidence_ids == ["ev_1"]
    assert sanitized.hypotheses[0].rejected_evidence_ids == ["ev_404"]
