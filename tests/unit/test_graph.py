"""Unit tests for the LangGraph multi-round investigation state machine."""

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from issuelens.agent.graph import create_investigation_graph
from issuelens.agent.llm import MockLLMClient
from issuelens.agent.nodes import InvestigationNodes, decide_next_step
from issuelens.agent.report import Hypothesis, InvestigationReportPayload
from issuelens.agent.state import InvestigationState
from issuelens.config import Settings
from issuelens.repository.snapshot import RepoSnapshot

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "sample_repo"


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        app_env="test",
        log_level="DEBUG",
        github_token=SecretStr("ghp_mock_token_for_tests_1234567890"),
        llm_provider="openai",
        llm_api_key=SecretStr("mock-api-key"),
        snapshot_cache_dir=".cache/test_snapshots",
        max_repository_bytes=10_000_000,
        max_file_bytes=1_000_000,
    )


@pytest.fixture
def repo_snapshot() -> RepoSnapshot:
    return RepoSnapshot(
        root_path=FIXTURE_DIR,
        repo_full_name="test-owner/test-repo",
        commit_sha="abcdef1234567890abcdef1234567890abcdef12",
    )


def create_sample_state(
    title: str = "Error connecting to pool in sample_pkg/database.py",
    body: str = "Encountered ConnectionError in database.py `connect` method.",
    max_rounds: int = 3,
    comments: list[dict[str, Any]] | None = None,
) -> InvestigationState:
    sample_comments = (
        comments
        if comments is not None
        else [
            {
                "id": 1,
                "user": "contributor",
                "body": "Check `DatabasePool` class in sample_pkg/database.py.",
                "created_at": "2026-10-20T11:00:00Z",
                "updated_at": None,
                "html_url": "https://github.com/test-owner/test-repo/issues/42#issuecomment-1",
            }
        ]
    )
    return {
        "run_id": "inv_test_run_001",
        "issue": {
            "number": 42,
            "title": title,
            "body": body,
            "state": "open",
            "author": "tester",
            "labels": ["bug"],
            "created_at": "2026-10-20T10:00:00Z",
            "updated_at": "2026-10-21T12:00:00Z",
            "closed_at": None,
            "comments_count": len(sample_comments),
            "html_url": "https://github.com/test-owner/test-repo/issues/42",
            "comments": sample_comments,
        },
        "repository": {
            "name": "test-repo",
            "full_name": "test-owner/test-repo",
            "owner": "test-owner",
            "default_branch": "main",
            "private": False,
            "html_url": "https://github.com/test-owner/test-repo",
            "description": "Sample test repository",
        },
        "commit_sha": "abcdef1234567890abcdef1234567890abcdef12",
        "issue_summary": {},
        "evidence": [],
        "hypotheses": [],
        "missing_information": [],
        "retrieval_round": 0,
        "tool_trace": [],
        "errors": [],
        "final_report": None,
        "max_retrieval_rounds": max_rounds,
        "followup_queries": [],
        "stop_reason": None,
        "observed_facts": [],
        "suggested_tests": [],
        "recommended_next_steps": [],
        "limitations": [],
        "verification_status": "not_run",
    }


# ==============================================================================
# 1. SUCCESS FLOW TEST
# ==============================================================================

@pytest.mark.asyncio
async def test_graph_success_flow(
    repo_snapshot: Any,
    test_settings: Settings,
) -> None:
    """Verify end-to-end execution of the LangGraph workflow produces a verified report."""
    mock_payload = InvestigationReportPayload(
        summary="Database pool connection failure due to unhandled timeout.",
        observed_facts=["ConnectionError raised when connecting to pool."],
        hypotheses=[
            Hypothesis(
                title="Missing timeout handling in connect",
                explanation="The connect method does not catch timeout errors properly.",
                confidence="high",
                confidence_rationale="Matches code found in database.py",
                supporting_evidence_ids=["ev_1"],
                contradicting_evidence_ids=[],
                verification_steps=["Inspect database.py"],
            )
        ],
        missing_information=[],
        suggested_tests=["tests/test_database.py"],
        recommended_next_steps=["Add retry logic with backoff."],
        limitations=["Only tested against mocked database."],
    )
    mock_llm = MockLLMClient(payload_override=mock_payload)
    nodes = InvestigationNodes(
        snapshot=repo_snapshot,
        llm_client=mock_llm,
        settings=test_settings,
    )
    graph = create_investigation_graph(nodes)
    initial_state = create_sample_state()

    final_state = await graph.ainvoke(initial_state)

    # Assert final report exists
    assert final_state["final_report"] is not None
    report = final_state["final_report"]
    assert report["run_id"] == "inv_test_run_001"
    assert report["verification_status"] == "verified"
    assert len(report["evidence"]) > 0

    # Assert tool trace captured all required stages
    tool_names = [t["tool_name"] for t in final_state["tool_trace"]]
    assert "parse_issue" in tool_names
    assert "retrieve_initial" in tool_names
    assert "form_hypotheses" in tool_names
    assert "evaluate_evidence" in tool_names
    assert "validate_citations" in tool_names
    assert "build_report" in tool_names

    # Assert retrieval round is at least 1
    assert final_state["retrieval_round"] >= 1


# ==============================================================================
# 2. TOOL FAILURE RESILIENCE TEST
# ==============================================================================

@pytest.mark.asyncio
async def test_graph_tool_failure_resilience(
    repo_snapshot: Any,
    test_settings: Settings,
) -> None:
    """Verify graph does not crash when a tool fails; logs error in state and produces report."""
    mock_llm = MockLLMClient()
    nodes = InvestigationNodes(
        snapshot=repo_snapshot,
        llm_client=mock_llm,
        settings=test_settings,
    )
    graph = create_investigation_graph(nodes)
    initial_state = create_sample_state()

    # Simulate an unexpected failure in the code search engine
    with patch(
        "issuelens.agent.nodes.search_code",
        side_effect=OSError("Ripgrep binary corrupted"),
    ):
        final_state = await graph.ainvoke(initial_state)

    # Graph must survive and return a completed state
    assert final_state["final_report"] is not None
    assert len(final_state["errors"]) > 0
    assert final_state["errors"][0]["node"] == "retrieve_initial"
    assert "Ripgrep binary corrupted" in final_state["errors"][0]["error"]

    # Tool trace must record the failure with status="error"
    failed_traces = [t for t in final_state["tool_trace"] if t["status"] == "error"]
    assert len(failed_traces) >= 1
    assert failed_traces[0]["tool_name"] == "retrieve_initial"
    assert failed_traces[0]["error_message"] is not None


# ==============================================================================
# 3. EMPTY RETRIEVAL TEST
# ==============================================================================

@pytest.mark.asyncio
async def test_graph_empty_retrieval_insufficient_evidence(
    repo_snapshot: Any,
    test_settings: Settings,
) -> None:
    """Verify graph handles empty search results by emitting an insufficient evidence report."""
    mock_payload = InvestigationReportPayload(
        summary="No code found matching query.",
        observed_facts=[],
        hypotheses=[],
        missing_information=["xyz_unobtainable_secret_log_token"],
        suggested_tests=[],
        recommended_next_steps=["Request minimal reproduction."],
        limitations=["No code found."],
    )
    mock_llm = MockLLMClient(payload_override=mock_payload)
    nodes = InvestigationNodes(
        snapshot=repo_snapshot,
        llm_client=mock_llm,
        settings=test_settings,
    )
    graph = create_investigation_graph(nodes)

    # State with query tokens that match zero files
    initial_state = create_sample_state(
        title="zzzjklmn_404_notfound_abc",
        body="xyzzy_9999_completely_unmatched_payload",
        comments=[],
    )

    final_state = await graph.ainvoke(initial_state)

    report = final_state["final_report"]
    assert report is not None
    assert len(report["evidence"]) == 0
    assert report["verification_status"] == "not_run"
    assert "[INSUFFICIENT EVIDENCE]" in report["summary"]


# ==============================================================================
# 4. LOOP CAP ENFORCEMENT TEST
# ==============================================================================

@pytest.mark.asyncio
async def test_graph_loop_cap_enforcement(
    repo_snapshot: Any,
    test_settings: Settings,
) -> None:
    """Verify follow-up retrieval loop terminates strictly when retrieval_round reaches loop cap."""
    mock_payload = InvestigationReportPayload(
        summary="Analysis requiring follow-up.",
        observed_facts=[],
        hypotheses=[
            Hypothesis(
                title="Potential flaw in auth module",
                explanation="Need to inspect `auth_handler` and `verify_password`.",
                confidence="low",
                confidence_rationale="Tentative hypothesis requiring more evidence",
                supporting_evidence_ids=[],
                contradicting_evidence_ids=[],
                verification_steps=["Inspect auth.py"],
            )
        ],
        missing_information=[
            "Missing auth_handler implementation",
            "Missing verify_password token",
        ],
        suggested_tests=[],
        recommended_next_steps=[],
        limitations=[],
    )
    mock_llm = MockLLMClient(payload_override=mock_payload)
    nodes = InvestigationNodes(
        snapshot=repo_snapshot,
        llm_client=mock_llm,
        settings=test_settings,
    )
    graph = create_investigation_graph(nodes)

    # Set loop cap to 2 rounds
    initial_state = create_sample_state(
        title="Check auth module in sample_pkg/auth.py",
        body="Need to inspect authentication handlers.",
        max_rounds=2,
    )

    final_state = await graph.ainvoke(initial_state)

    # The retrieval round must have reached the cap of 2 and stopped
    assert final_state["retrieval_round"] == 2

    # Assert follow-up retrieval was invoked exactly once
    followup_traces = [
        t for t in final_state["tool_trace"] if t["tool_name"] == "retrieve_followup"
    ]
    assert len(followup_traces) == 1
    assert followup_traces[0]["round"] == 2

    # Assert graph proceeded to validation and report building
    assert final_state["final_report"] is not None


# ==============================================================================
# 5. CONDITIONAL ROUTING UNIT TESTS
# ==============================================================================

def test_decide_next_step_routes() -> None:
    """Verify decide_next_step router adheres strictly to termination rules."""
    # 1. Loop cap reached
    state_capped: InvestigationState = {
        "retrieval_round": 3,
        "max_retrieval_rounds": 3,
        "followup_queries": ["query1", "query2"],
    }
    assert decide_next_step(state_capped) == "validate_citations"

    # 2. No remaining queries
    state_empty_queries: InvestigationState = {
        "retrieval_round": 1,
        "max_retrieval_rounds": 3,
        "followup_queries": [],
    }
    assert decide_next_step(state_empty_queries) == "validate_citations"

    # 3. Valid follow-up condition
    state_continue: InvestigationState = {
        "retrieval_round": 1,
        "max_retrieval_rounds": 3,
        "followup_queries": ["query1"],
    }
    assert decide_next_step(state_continue) == "retrieve_followup"


# ==============================================================================
# 6. PARSE ISSUE NODE EXTRACTION UNIT TESTS
# ==============================================================================

@pytest.mark.asyncio
async def test_parse_issue_node_extraction(
    test_settings: Settings,
) -> None:
    """Verify parse_issue correctly extracts stack frames, error types, and symbols."""
    nodes = InvestigationNodes(
        snapshot=None,
        llm_client=MockLLMClient(),
        settings=test_settings,
    )
    state = create_sample_state(
        title="Crash with ValueError in worker.py",
        body=(
            'Traceback (most recent call last):\n'
            '  File "sample_pkg/auth.py", line 15, in authenticate\n'
            'ValueError: invalid token format\n'
            'Also check `generate_token` in utils.py'
        ),
    )

    result = await nodes.parse_issue(state)
    summary = result["issue_summary"]

    assert "ValueError" in summary["error_types"]
    assert any("sample_pkg/auth.py:15" in f for f in summary["stack_trace_frames"])
    assert "generate_token" in summary["extracted_symbols"]
    matched_target = (
        "sample_pkg/auth.py" in summary["target_files"]
        or "auth.py" in str(summary["target_files"])
    )
    assert matched_target
    # High-signal identifiers must lead the retrieval query; prose comes last.
    assert summary["raw_query"].startswith("ValueError")
    assert len(result["tool_trace"]) == 1
    assert result["tool_trace"][0]["tool_name"] == "parse_issue"


@pytest.mark.asyncio
async def test_evaluate_evidence_ignores_prose_missing_information(
    test_settings: Settings,
) -> None:
    """Verify follow-up retrieval is driven by identifiers, not prose sentences."""
    nodes = InvestigationNodes(
        snapshot=None,
        llm_client=MockLLMClient(),
        settings=test_settings,
    )
    state: InvestigationState = {
        "hypotheses": [],
        "evidence": [],
        "missing_information": [
            "Exact Python version in user environment was not specified.",
            "Missing `auth_handler` implementation",
        ],
        "retrieval_round": 1,
        "tool_trace": [],
    }

    result = await nodes.evaluate_evidence(state)

    assert result["followup_queries"] == ["auth_handler"]

