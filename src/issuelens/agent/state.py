"""TypedDict definitions for LangGraph investigation state and tool execution traces."""

from typing import Any, Literal, TypedDict


class ToolTraceItem(TypedDict, total=False):
    """Audit log entry capturing deterministic tool or node execution."""

    tool_name: str
    round: int
    inputs: dict[str, Any]
    output_summary: str
    status: Literal["success", "error", "skipped"]
    duration_ms: float
    error_message: str | None


class IssueSummary(TypedDict, total=False):
    """Structured synthesis of raw GitHub issue content."""

    title: str
    symptoms: list[str]
    error_types: list[str]
    stack_trace_frames: list[str]
    extracted_symbols: list[str]
    target_files: list[str]
    raw_query: str


class InvestigationState(TypedDict, total=False):
    """Authoritative state dictionary threaded through the LangGraph workflow.

    Conforms directly to Section 6.2 of docs/BUILD_SPECIFICATION.md.
    """

    run_id: str
    issue: dict[str, Any]
    repository: dict[str, Any]
    commit_sha: str
    issue_summary: dict[str, Any]
    evidence: list[dict[str, Any]]
    hypotheses: list[dict[str, Any]]
    missing_information: list[str]
    retrieval_round: int
    tool_trace: list[ToolTraceItem]
    errors: list[dict[str, Any]]
    final_report: dict[str, Any] | None

    # Auxiliary workflow control attributes
    max_retrieval_rounds: int
    followup_queries: list[str]
    stop_reason: str | None
    observed_facts: list[str]
    suggested_tests: list[str]
    recommended_next_steps: list[str]
    limitations: list[str]
    verification_status: str

