"""Unit tests for prompt construction and prompt injection isolation boundaries."""

from datetime import UTC, datetime

from issuelens.agent.prompts import (
    build_investigation_messages,
    build_investigation_prompt,
    format_evidence_block,
)
from issuelens.agent.report import EvidenceItem
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
)


def create_sample_context() -> GitHubIssueContext:
    """Helper creating a standard GitHubIssueContext for prompt tests."""
    issue = GitHubIssue(
        number=42,
        title="ZeroDivisionError when batch size is 0",
        body="Executing batch_process(items, batch_size=0) raises ZeroDivisionError.",
        state="open",
        author="alice",
        labels=["bug", "priority-high"],
        created_at=datetime.now(tz=UTC),
        comments_count=1,
        html_url="https://github.com/example/calc/issues/42",
    )
    comments = [
        GitHubComment(
            id=101,
            author="bob",
            body="Can confirm. The loop uses `total // batch_size` directly.",
            created_at=datetime.now(tz=UTC),
        )
    ]
    return GitHubIssueContext(
        issue_url="https://github.com/example/calc/issues/42",
        repository="example/calc",
        issue_number=42,
        default_branch="main",
        commit_sha="fedcba9876543210fedcba9876543210fedcba98",
        issue=issue,
        comments=comments,
        fetched_at=datetime.now(tz=UTC),
    )


def test_format_evidence_block_empty() -> None:
    """Verify empty evidence list produces clear fallback message."""
    output = format_evidence_block([])
    assert output == "No repository code references retrieved."


def test_format_evidence_block_populated() -> None:
    """Verify evidence block formats citations with line ranges and excerpts."""
    evidence = [
        EvidenceItem(
            evidence_id="ev_1",
            source_type="code",
            path_or_url="calc/batch.py",
            start_line=15,
            end_line=20,
            excerpt=(
                "15: def batch_process(items, batch_size):\n"
                "16:     count = len(items) // batch_size"
            ),
            commit_sha="fedcba9876543210fedcba9876543210fedcba98",
        )
    ]
    block = format_evidence_block(evidence)
    assert "[ev_1] Source: calc/batch.py (lines 15-20)" in block
    assert "count = len(items) // batch_size" in block


def test_build_investigation_prompt_structure_and_boundaries() -> None:
    """Verify prompt builder encloses untrusted content in XML tags with safety directives."""
    context = create_sample_context()
    evidence = [
        EvidenceItem(
            evidence_id="ev_1",
            source_type="code",
            path_or_url="calc/batch.py",
            start_line=15,
            end_line=20,
            excerpt="15: def batch_process(): pass",
            commit_sha="fedcba9876543210fedcba9876543210fedcba98",
        )
    ]

    system_prompt, user_prompt = build_investigation_prompt(context, evidence)

    # Verify system prompt safety rules
    assert "PASSIVE UNTRUSTED DATA" in system_prompt
    assert "verification_status" in system_prompt
    assert "not_run" in system_prompt
    assert "NEVER fabricate citations" in system_prompt

    # Verify user prompt isolation tags
    assert "<untrusted_issue_data>" in user_prompt
    assert "</untrusted_issue_data>" in user_prompt
    assert "<untrusted_repository_evidence>" in user_prompt
    assert "</untrusted_repository_evidence>" in user_prompt

    # Verify payload elements
    assert "ZeroDivisionError when batch size is 0" in user_prompt
    assert "fedcba9876543210fedcba9876543210fedcba98" in user_prompt
    assert "[ev_1]" in user_prompt


def test_build_investigation_messages() -> None:
    """Verify LangChain messages are correctly constructed."""
    context = create_sample_context()
    messages = build_investigation_messages(context, [])
    assert len(messages) == 2
    assert messages[0].type == "system"
    assert messages[1].type == "human"
    assert "PASSIVE UNTRUSTED DATA" in str(messages[0].content)
    assert "<untrusted_issue_data>" in str(messages[1].content)

