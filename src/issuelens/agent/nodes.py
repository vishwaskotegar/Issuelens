"""Explicit execution nodes and conditional routers for the LangGraph agent workflow."""

import re
import time
from datetime import UTC, datetime
from typing import Any, Literal

from issuelens.agent.llm import LLMClientProtocol
from issuelens.agent.prompts import build_investigation_messages
from issuelens.agent.report import (
    EvidenceItem,
    Hypothesis,
    InvestigationReport,
    InvestigationReportPayload,
)
from issuelens.agent.state import InvestigationState, IssueSummary, ToolTraceItem
from issuelens.agent.validator import validate_investigation_report
from issuelens.config import Settings
from issuelens.core.errors import IssueLensError
from issuelens.core.logging import get_logger
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
)
from issuelens.repository.code_search import (
    classify_path,
    extract_search_terms,
    is_high_signal_term,
    search_code,
)
from issuelens.repository.snapshot import RepoSnapshot

logger = get_logger("issuelens.agent.nodes")


def _parse_datetime(val: Any) -> datetime:
    """Safely parse datetime from ISO string or return current UTC datetime."""
    if isinstance(val, datetime):
        return val
    if isinstance(val, str):
        try:
            return datetime.fromisoformat(val.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


def get_context_from_state(state: InvestigationState) -> GitHubIssueContext:
    """Reconstruct a typed GitHubIssueContext from serializable state dictionaries."""
    issue_data = state.get("issue", {})
    repo_data = state.get("repository", {})
    raw_comments = issue_data.get("comments", [])

    comments: list[GitHubComment] = []
    for idx, c in enumerate(raw_comments, start=1):
        if isinstance(c, GitHubComment):
            comments.append(c)
        elif isinstance(c, dict):
            comments.append(
                GitHubComment(
                    id=int(c.get("id", idx)),
                    author=str(c.get("author") or c.get("user") or "commenter"),
                    body=str(c.get("body", "")),
                    created_at=_parse_datetime(c.get("created_at")),
                    updated_at=(
                        _parse_datetime(c.get("updated_at")) if c.get("updated_at") else None
                    ),
                    html_url=c.get("html_url"),
                )
            )

    issue = GitHubIssue(
        number=int(issue_data.get("number", 1)),
        title=str(issue_data.get("title", "")),
        body=str(issue_data.get("body", "")),
        state=str(issue_data.get("state", "open")),
        author=str(issue_data.get("author") or "author"),
        labels=list(issue_data.get("labels", [])),
        created_at=_parse_datetime(issue_data.get("created_at")),
        updated_at=(
            _parse_datetime(issue_data.get("updated_at"))
            if issue_data.get("updated_at")
            else None
        ),
        closed_at=(
            _parse_datetime(issue_data.get("closed_at"))
            if issue_data.get("closed_at")
            else None
        ),
        comments_count=len(comments),
        html_url=str(issue_data.get("html_url", "")),
        is_pull_request=False,
    )

    repo_full = str(repo_data.get("full_name") or "owner/repo")

    return GitHubIssueContext(
        issue_url=issue.html_url or f"https://github.com/{repo_full}/issues/{issue.number}",
        repository=repo_full,
        issue_number=issue.number,
        default_branch=str(repo_data.get("default_branch", "main")),
        commit_sha=str(state.get("commit_sha", "unknown")),
        issue=issue,
        comments=comments,
        fetched_at=datetime.now(UTC),
    )


def decide_next_step(
    state: InvestigationState,
) -> Literal["retrieve_followup", "validate_citations"]:
    """Conditional router deciding whether to perform follow-up retrieval or proceed to validation.

    Stop Conditions (exits loop to 'validate_citations'):
    1. Loop Cap: retrieval_round >= max_retrieval_rounds (default: 3).
    2. No Remaining Queries: followup_queries queue is empty.
    3. Terminal Insufficient Evidence: no evidence retrieved and no remaining queries.
    """
    max_rounds = state.get("max_retrieval_rounds", 3)
    current_round = state.get("retrieval_round", 1)
    followup_queries = state.get("followup_queries", [])

    if current_round >= max_rounds:
        logger.info(
            "Workflow routing: loop cap reached (%d >= %d); proceeding to citation validation",
            current_round,
            max_rounds,
        )
        return "validate_citations"

    if not followup_queries:
        logger.info(
            "Workflow routing: no follow-up queries at round %d; proceeding to validation",
            current_round,
        )
        return "validate_citations"

    logger.info(
        "Workflow routing: pursuing follow-up retrieval round %d with %d queued queries",
        current_round + 1,
        len(followup_queries),
    )
    return "retrieve_followup"


class InvestigationNodes:
    """Encapsulates executable graph node implementations with injected dependencies."""

    def __init__(
        self,
        snapshot: RepoSnapshot | None,
        llm_client: LLMClientProtocol,
        settings: Settings,
    ) -> None:
        self.snapshot = snapshot
        self.llm_client = llm_client
        self.settings = settings

    async def parse_issue(self, state: InvestigationState) -> dict[str, Any]:
        """Node 1: Parse raw issue text, extracting stack traces, error types, and symbols."""
        start_time = time.perf_counter()
        issue_data = state.get("issue", {})
        title = issue_data.get("title", "")
        body = issue_data.get("body", "") or ""
        comments = issue_data.get("comments", [])

        combined_text = f"{title}\n{body}\n"
        for c in comments[:3]:
            c_body = c.get("body", "") if isinstance(c, dict) else getattr(c, "body", "")
            combined_text += f"\n{c_body}"

        # 1. Extract error names (e.g. ValueError, RequestException)
        error_types = list(
            dict.fromkeys(
                re.findall(
                    r"\b([A-Z][a-zA-Z0-9]*(?:Error|Exception|Warning))\b",
                    combined_text,
                )
            )
        )

        # 2. Extract referenced python filenames
        target_files = list(
            dict.fromkeys(re.findall(r"\b([a-zA-Z0-9_\-\./]+\.py)\b", combined_text))
        )

        # 3. Extract backticked symbols or function calls
        extracted_symbols = list(
            dict.fromkeys(re.findall(r"`([a-zA-Z_][a-zA-Z0-9_]+)`", combined_text))
        )

        # 4. Extract stack trace frames
        stack_trace_frames = re.findall(
            r'File "([^"]+)", line (\d+)(?:, in (\w+))?', combined_text
        )
        frames_summary = [
            f"{f[0]}:{f[1]}" + (f" ({f[2]})" if f[2] else "")
            for f in stack_trace_frames
        ]

        # 5. Extract symptoms
        symptoms: list[str] = [title]
        if error_types:
            symptoms.append(f"Encountered error types: {', '.join(error_types)}")

        # Compile initial search query. High-signal identifiers (error types, file
        # paths, symbols, stack frames) must come before natural-language prose so
        # retrieval is not dominated by generic English words from the body.
        query_parts: list[str] = []
        query_parts.extend(error_types[:5])
        query_parts.extend(target_files[:5])
        query_parts.extend(extracted_symbols[:8])
        query_parts.extend(frames_summary[:5])
        query_parts.append(title)
        if body:
            query_parts.append(body[:800])

        raw_query = " ".join(part for part in query_parts if part)

        issue_summary: IssueSummary = {
            "title": title,
            "symptoms": symptoms,
            "error_types": error_types,
            "stack_trace_frames": frames_summary,
            "extracted_symbols": extracted_symbols,
            "target_files": target_files,
            "raw_query": raw_query,
        }

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        trace_entry: ToolTraceItem = {
            "tool_name": "parse_issue",
            "round": 0,
            "inputs": {"title": title, "comments_count": len(comments)},
            "output_summary": (
                f"Extracted {len(error_types)} error types, {len(target_files)} files, "
                f"and {len(extracted_symbols)} symbols."
            ),
            "status": "success",
            "duration_ms": elapsed_ms,
            "error_message": None,
        }

        tool_trace = list(state.get("tool_trace", []))
        tool_trace.append(trace_entry)

        return {
            "issue_summary": issue_summary,
            "tool_trace": tool_trace,
        }

    async def retrieve_initial(self, state: InvestigationState) -> dict[str, Any]:
        """Node 2: Execute deterministic initial lexical and AST code search."""
        start_time = time.perf_counter()
        tool_trace = list(state.get("tool_trace", []))
        errors = list(state.get("errors", []))
        issue_summary = state.get("issue_summary", {})
        query = issue_summary.get("raw_query") or state.get("issue", {}).get("title", "")

        evidence_items: list[dict[str, Any]] = []
        status: Literal["success", "error", "skipped"] = "success"
        error_msg: str | None = None
        output_summary = "Zero code hits found."

        if self.snapshot is None:
            status = "skipped"
            output_summary = "Skipped initial retrieval: no repository snapshot available."
        else:
            try:
                code_hits = search_code(
                    query=query,
                    repo_snapshot=self.snapshot,
                    max_results=10,
                )
                evidence_items = [
                    EvidenceItem(
                        evidence_id=f"ev_{idx}",
                        source_type=classify_path(hit.path),
                        path_or_url=hit.path,
                        start_line=hit.start_line,
                        end_line=hit.end_line,
                        excerpt=hit.excerpt,
                        commit_sha=hit.commit_sha,
                    ).model_dump()
                    for idx, hit in enumerate(code_hits, start=1)
                ]
                output_summary = f"Retrieved {len(evidence_items)} initial code evidence hits."
            except Exception as exc:
                status = "error"
                error_msg = f"Initial code retrieval error: {exc}"
                logger.warning(error_msg, exc_info=True)
                errors.append({"node": "retrieve_initial", "error": str(exc)})
                output_summary = f"Code search failed: {exc}"

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        trace_entry: ToolTraceItem = {
            "tool_name": "retrieve_initial",
            "round": 1,
            "inputs": {"query": query[:200]},
            "output_summary": output_summary,
            "status": status,
            "duration_ms": elapsed_ms,
            "error_message": error_msg,
        }
        tool_trace.append(trace_entry)

        return {
            "evidence": evidence_items,
            "retrieval_round": 1,
            "tool_trace": tool_trace,
            "errors": errors,
        }

    async def form_hypotheses(self, state: InvestigationState) -> dict[str, Any]:
        """Node 3: Prompt LLM for testable root cause hypotheses and missing info."""
        start_time = time.perf_counter()
        tool_trace = list(state.get("tool_trace", []))
        errors = list(state.get("errors", []))

        context = get_context_from_state(state)
        raw_evidence = state.get("evidence", [])
        evidence_items = [EvidenceItem.model_validate(e) for e in raw_evidence]

        messages = build_investigation_messages(context, evidence_items)

        hypotheses_dicts: list[dict[str, Any]] = []
        missing_info: list[str] = []
        observed_facts: list[str] = []
        suggested_tests: list[str] = []
        recommended_next_steps: list[str] = []
        limitations: list[str] = []
        status: Literal["success", "error", "skipped"] = "success"
        error_msg: str | None = None
        output_summary = "Formed hypotheses from evidence."

        try:
            payload: InvestigationReportPayload = (
                await self.llm_client.generate_report_payload(messages)
            )
            for h in payload.hypotheses:
                hd = h.model_dump()
                hd["support_status"] = "tentative"
                hypotheses_dicts.append(hd)

            missing_info = list(payload.missing_information)
            observed_facts = list(payload.observed_facts)
            suggested_tests = list(payload.suggested_tests)
            recommended_next_steps = list(payload.recommended_next_steps)
            limitations = list(payload.limitations)
            output_summary = (
                f"Generated {len(hypotheses_dicts)} hypotheses and "
                f"{len(missing_info)} missing info items."
            )
        except Exception as exc:
            if isinstance(exc, IssueLensError):
                raise
            status = "error"
            error_msg = f"LLM hypothesis generation error: {exc}"
            logger.warning(error_msg, exc_info=True)
            errors.append({"node": "form_hypotheses", "error": str(exc)})
            output_summary = f"Hypothesis generation failed: {exc}"
            # Fallback hypothesis to allow graceful recovery
            hypotheses_dicts = [
                Hypothesis(
                    title="Investigation inconclusive due to inference error",
                    explanation=f"Hypothesis generation encountered an error: {exc}",
                    confidence="low",
                    confidence_rationale="Inference provider encountered an error.",
                    supporting_evidence_ids=[],
                    contradicting_evidence_ids=[],
                    verification_steps=["Inspect LLM error diagnostics."],
                ).model_dump()
            ]

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        trace_entry: ToolTraceItem = {
            "tool_name": "form_hypotheses",
            "round": state.get("retrieval_round", 1),
            "inputs": {"evidence_count": len(evidence_items)},
            "output_summary": output_summary,
            "status": status,
            "duration_ms": elapsed_ms,
            "error_message": error_msg,
        }
        tool_trace.append(trace_entry)

        return {
            "hypotheses": hypotheses_dicts,
            "missing_information": missing_info,
            "observed_facts": observed_facts,
            "suggested_tests": suggested_tests,
            "recommended_next_steps": recommended_next_steps,
            "limitations": limitations,
            "tool_trace": tool_trace,
            "errors": errors,
        }

    async def evaluate_evidence(self, state: InvestigationState) -> dict[str, Any]:
        """Node 4: Evaluate evidence for hypotheses and queue follow-up search queries."""
        start_time = time.perf_counter()
        tool_trace = list(state.get("tool_trace", []))
        hypotheses = list(state.get("hypotheses", []))
        evidence = state.get("evidence", [])
        missing_info = state.get("missing_information", [])
        available_ids = {e["evidence_id"] for e in evidence}

        followup_queries: list[str] = []
        supported_count = 0

        # 1. Update support status for each hypothesis
        for h in hypotheses:
            supp_ids = h.get("supporting_evidence_ids", [])
            valid_supporting = [eid for eid in supp_ids if eid in available_ids]
            if valid_supporting:
                h["support_status"] = "supported"
                supported_count += 1
            else:
                h["support_status"] = "unsupported"

        # 2. Formulate follow-up queries from missing information & unsupported hypotheses.
        # Only identifier-like/path-like terms are used: converting a natural-language
        # "missing information" sentence into a literal search floods results with prose.
        for item in missing_info:
            terms = [
                term for term in extract_search_terms(item) if is_high_signal_term(term)
            ]
            if terms:
                candidate = " ".join(terms[:5])
                if candidate not in followup_queries:
                    followup_queries.append(candidate)

        # Also extract referenced symbol names from unsupported hypotheses
        for h in hypotheses:
            if h.get("support_status") != "supported":
                symbols = re.findall(r"`([a-zA-Z_]\w+)`", str(h.get("explanation", "")))
                for s in symbols:
                    if s not in followup_queries:
                        followup_queries.append(s)

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        trace_entry: ToolTraceItem = {
            "tool_name": "evaluate_evidence",
            "round": state.get("retrieval_round", 1),
            "inputs": {"hypotheses_count": len(hypotheses), "evidence_count": len(evidence)},
            "output_summary": (
                f"Evaluated {len(hypotheses)} hypotheses: {supported_count} supported. "
                f"Queued {len(followup_queries)} follow-up queries."
            ),
            "status": "success",
            "duration_ms": elapsed_ms,
            "error_message": None,
        }
        tool_trace.append(trace_entry)

        return {
            "hypotheses": hypotheses,
            "followup_queries": followup_queries,
            "tool_trace": tool_trace,
        }

    async def retrieve_followup(self, state: InvestigationState) -> dict[str, Any]:
        """Node 5: Execute bounded secondary search round for targeted missing evidence."""
        start_time = time.perf_counter()
        tool_trace = list(state.get("tool_trace", []))
        errors = list(state.get("errors", []))
        evidence = list(state.get("evidence", []))
        followup_queries = list(state.get("followup_queries", []))
        current_round = state.get("retrieval_round", 1) + 1

        status: Literal["success", "error", "skipped"] = "success"
        error_msg: str | None = None
        new_evidence_count = 0
        executed_query = ""

        if self.snapshot is None:
            status = "skipped"
            output_summary = "Skipped follow-up retrieval: no repository snapshot."
        elif not followup_queries:
            status = "skipped"
            output_summary = "Skipped follow-up retrieval: no queries queued."
        else:
            executed_query = followup_queries.pop(0)
            try:
                hits = search_code(
                    query=executed_query,
                    repo_snapshot=self.snapshot,
                    max_results=5,
                )
                existing_keys = {
                    (e.get("path_or_url"), e.get("start_line"))
                    for e in evidence
                }
                for hit in hits:
                    if (hit.path, hit.start_line) not in existing_keys:
                        new_item = EvidenceItem(
                            evidence_id=f"ev_{len(evidence) + 1}",
                            source_type=classify_path(hit.path),
                            path_or_url=hit.path,
                            start_line=hit.start_line,
                            end_line=hit.end_line,
                            excerpt=hit.excerpt,
                            commit_sha=hit.commit_sha,
                        )
                        evidence.append(new_item.model_dump())
                        existing_keys.add((hit.path, hit.start_line))
                        new_evidence_count += 1

                output_summary = (
                    f"Follow-up retrieval added {new_evidence_count} new evidence items."
                )
            except Exception as exc:
                status = "error"
                error_msg = f"Follow-up search error: {exc}"
                logger.warning(error_msg, exc_info=True)
                errors.append({"node": "retrieve_followup", "error": str(exc)})
                output_summary = f"Follow-up search failed: {exc}"

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        trace_entry: ToolTraceItem = {
            "tool_name": "retrieve_followup",
            "round": current_round,
            "inputs": {"query": executed_query[:100]},
            "output_summary": output_summary,
            "status": status,
            "duration_ms": elapsed_ms,
            "error_message": error_msg,
        }
        tool_trace.append(trace_entry)

        return {
            "evidence": evidence,
            "retrieval_round": current_round,
            "followup_queries": followup_queries,
            "tool_trace": tool_trace,
            "errors": errors,
        }

    async def validate_citations(self, state: InvestigationState) -> dict[str, Any]:
        """Node 6: Validate code citations against physical repository snapshot on disk."""
        start_time = time.perf_counter()
        tool_trace = list(state.get("tool_trace", []))
        raw_evidence = state.get("evidence", [])
        raw_hypotheses = state.get("hypotheses", [])

        evidence_items = [EvidenceItem.model_validate(e) for e in raw_evidence]
        hypotheses = [Hypothesis.model_validate(h) for h in raw_hypotheses]

        proto_report = InvestigationReport(
            run_id=state.get("run_id", "inv_unknown"),
            issue_url=state.get("issue", {}).get("html_url", ""),
            repository=state.get("repository", {}).get("full_name", ""),
            commit_sha=state.get("commit_sha", "unknown"),
            summary="Preliminary report awaiting validation",
            observed_facts=state.get("observed_facts", []),
            hypotheses=hypotheses,
            evidence=evidence_items,
            missing_information=state.get("missing_information", []),
            suggested_tests=state.get("suggested_tests", []),
            recommended_next_steps=state.get("recommended_next_steps", []),
            verification_status="not_run",
            limitations=state.get("limitations", []),
        )

        output_summary = "Citations validated."
        if self.snapshot is not None:
            validated_report, validation_result = validate_investigation_report(
                proto_report, self.snapshot
            )
            output_evidence = [e.model_dump() for e in validated_report.evidence]
            output_hypotheses = [h.model_dump() for h in validated_report.hypotheses]
            verification_status = validated_report.verification_status
            output_summary = (
                f"Validation finished: {verification_status} "
                f"({len(validation_result.valid_evidence_ids)} valid, "
                f"{len(validation_result.invalid_evidence_ids)} invalid citations)."
            )
        else:
            output_evidence = [e.model_dump() for e in evidence_items]
            output_hypotheses = [h.model_dump() for h in hypotheses]
            verification_status = "not_run"
            output_summary = "Validation skipped: snapshot unavailable; status marked not_run."

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        trace_entry: ToolTraceItem = {
            "tool_name": "validate_citations",
            "round": state.get("retrieval_round", 1),
            "inputs": {"evidence_count": len(evidence_items)},
            "output_summary": output_summary,
            "status": "success",
            "duration_ms": elapsed_ms,
            "error_message": None,
        }
        tool_trace.append(trace_entry)

        return {
            "evidence": output_evidence,
            "hypotheses": output_hypotheses,
            "verification_status": verification_status,
            "tool_trace": tool_trace,
        }

    async def build_report(self, state: InvestigationState) -> dict[str, Any]:
        """Node 7: Construct final validated InvestigationReport conforming to Pydantic schema."""
        start_time = time.perf_counter()
        tool_trace = list(state.get("tool_trace", []))

        issue_data = state.get("issue", {})
        repo_data = state.get("repository", {})
        evidence_items = [EvidenceItem.model_validate(e) for e in state.get("evidence", [])]
        hypotheses = [Hypothesis.model_validate(h) for h in state.get("hypotheses", [])]
        verification_status = state.get("verification_status", "not_run")

        summary_text = (
            f"Investigation of {repo_data.get('full_name')}#{issue_data.get('number')}: "
            f"{issue_data.get('title')}"
        )

        if not evidence_items:
            summary_text = (
                f"[INSUFFICIENT EVIDENCE] Unable to locate verifiable source code evidence "
                f"for issue {issue_data.get('number')}. {summary_text}"
            )
            verification_status = "not_run"

        final_report = InvestigationReport(
            run_id=state.get("run_id", "inv_unknown"),
            issue_url=issue_data.get("html_url", ""),
            repository=repo_data.get("full_name", ""),
            commit_sha=state.get("commit_sha", "unknown"),
            summary=summary_text,
            observed_facts=state.get("observed_facts", []),
            hypotheses=hypotheses,
            evidence=evidence_items,
            missing_information=state.get("missing_information", []),
            suggested_tests=state.get("suggested_tests", []),
            recommended_next_steps=state.get("recommended_next_steps", []),
            verification_status=verification_status,  # type: ignore[arg-type]
            limitations=state.get("limitations", []),
        )

        elapsed_ms = (time.perf_counter() - start_time) * 1000.0
        trace_entry: ToolTraceItem = {
            "tool_name": "build_report",
            "round": state.get("retrieval_round", 1),
            "inputs": {"run_id": final_report.run_id},
            "output_summary": (
                f"Report built with verification_status='{final_report.verification_status}'."
            ),
            "status": "success",
            "duration_ms": elapsed_ms,
            "error_message": None,
        }
        tool_trace.append(trace_entry)

        return {
            "final_report": final_report.model_dump(),
            "verification_status": final_report.verification_status,
            "tool_trace": tool_trace,
        }
