"""Investigation service orchestrating issue ingestion, code search, and report generation."""

import json
import re
import uuid
from pathlib import Path
from typing import Any

from issuelens.agent.graph import create_investigation_graph
from issuelens.agent.llm import LLMClientProtocol, get_llm_client
from issuelens.agent.nodes import InvestigationNodes
from issuelens.agent.report import InvestigationReport
from issuelens.agent.state import InvestigationState
from issuelens.config import Settings, get_settings
from issuelens.core.errors import IssueLensError, LLMOutputValidationError
from issuelens.core.logging import get_logger
from issuelens.github.client import GitHubClient, GitHubClientProtocol
from issuelens.github.models import GitHubIssueContext
from issuelens.github.url_parser import IssueTarget, parse_issue_target
from issuelens.repository.snapshot import (
    RepoSnapshot,
    cache_and_create_snapshot_from_zip,
    get_cached_snapshot,
)

logger = get_logger("issuelens.agent.service")


def extract_json_payload(raw_text: str) -> dict[str, Any]:
    """Extract and parse a JSON dictionary from an LLM response string."""
    text = raw_text.strip()

    # Strip markdown code blocks if present
    if "```" in text:
        pattern = r"```(?:json)?\s*([\s\S]*?)\s*```"
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            text = match.group(1).strip()

    # Delineate outer JSON object boundaries
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        text = text[start : end + 1]

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise LLMOutputValidationError("LLM response did not produce a JSON object dictionary.")
        return data
    except json.JSONDecodeError as exc:
        raise LLMOutputValidationError(
            f"Failed to parse LLM response as valid JSON: {exc}"
        ) from exc


def compile_search_query(context: GitHubIssueContext) -> str:
    """Extract lexical search tokens from issue title, body, and comments."""
    query_parts: list[str] = [context.issue.title]
    if context.issue.body:
        query_parts.append(context.issue.body[:1500])
    for comment in context.comments[:3]:
        query_parts.append(comment.body[:500])
    return " ".join(query_parts)


class InvestigationService:
    """Service orchestrating evidence-backed investigation report generation via LangGraph."""

    def __init__(
        self,
        github_client: GitHubClientProtocol,
        llm_client: LLMClientProtocol,
        settings: Settings,
    ) -> None:
        self.github_client = github_client
        self.llm_client = llm_client
        self.settings = settings

    async def investigate(
        self,
        target_or_url: str | IssueTarget,
        snapshot: RepoSnapshot | None = None,
        run_id: str | None = None,
    ) -> InvestigationReport:
        """Run complete investigation flow for a target issue via LangGraph state machine.

        Args:
            target_or_url: GitHub issue URL string or parsed IssueTarget.
            snapshot: Optional pre-existing RepoSnapshot (e.g. for testing or local cache).
            run_id: Optional predetermined run identifier.

        Returns:
            Validated InvestigationReport with pinned commit SHA and evidence citations.
        """
        active_run_id = run_id or f"inv_{uuid.uuid4().hex[:12]}"
        target = (
            parse_issue_target(target_or_url)
            if isinstance(target_or_url, str)
            else target_or_url
        )

        logger.info(
            "Starting investigation %s for %s/%s#%d via LangGraph workflow",
            active_run_id,
            target.owner,
            target.repo,
            target.issue_number,
        )

        # 1. Fetch issue context and pinned commit SHA
        context = await self.github_client.fetch_issue_context(target)

        # 2. Acquire repository snapshot if not supplied
        active_snapshot = snapshot
        if active_snapshot is None:
            cache_dir = Path(self.settings.snapshot_cache_dir)
            active_snapshot = get_cached_snapshot(
                cache_dir=cache_dir,
                owner=target.owner,
                repo=target.repo,
                commit_sha=context.commit_sha,
                max_file_bytes=self.settings.max_file_bytes,
            )
            if active_snapshot is not None:
                logger.info(
                    "Reusing cached repository snapshot for %s at %s (%s)",
                    target,
                    context.commit_sha,
                    active_snapshot.root_path,
                )
            else:
                logger.info(
                    "Downloading repository archive for %s at %s",
                    target,
                    context.commit_sha,
                )
                zip_bytes = await self.github_client.download_archive(
                    owner=target.owner,
                    repo=target.repo,
                    ref=context.commit_sha,
                    max_bytes=self.settings.max_repository_bytes,
                )
                active_snapshot = cache_and_create_snapshot_from_zip(
                    cache_dir=cache_dir,
                    owner=target.owner,
                    repo=target.repo,
                    commit_sha=context.commit_sha,
                    zip_bytes=zip_bytes,
                    max_repository_bytes=self.settings.max_repository_bytes,
                    max_file_bytes=self.settings.max_file_bytes,
                    max_cached_snapshots=self.settings.max_cached_snapshots,
                    max_cache_total_bytes=self.settings.max_cache_total_bytes,
                )

        repo_full = context.repository
        owner_name, repo_name = (
            repo_full.split("/", 1) if "/" in repo_full else (repo_full, repo_full)
        )

        # 3. Construct initial LangGraph workflow state
        initial_state: InvestigationState = {
            "run_id": active_run_id,
            "issue": {
                "number": context.issue.number,
                "title": context.issue.title,
                "body": context.issue.body,
                "state": context.issue.state,
                "author": context.issue.author,
                "labels": context.issue.labels,
                "created_at": context.issue.created_at,
                "updated_at": context.issue.updated_at,
                "closed_at": context.issue.closed_at,
                "comments_count": context.issue.comments_count,
                "html_url": context.issue.html_url,
                "comments": [c.model_dump() for c in context.comments],
            },
            "repository": {
                "name": repo_name,
                "full_name": repo_full,
                "owner": owner_name,
                "default_branch": context.default_branch,
                "private": False,
                "html_url": f"https://github.com/{repo_full}",
                "description": None,
            },
            "commit_sha": context.commit_sha,
            "issue_summary": {},
            "evidence": [],
            "hypotheses": [],
            "missing_information": [],
            "retrieval_round": 0,
            "tool_trace": [],
            "errors": [],
            "final_report": None,
            "max_retrieval_rounds": 3,
            "followup_queries": [],
            "stop_reason": None,
            "observed_facts": [],
            "suggested_tests": [],
            "recommended_next_steps": [],
            "limitations": [],
            "verification_status": "not_run",
        }

        # 4. Instantiate nodes and compile LangGraph state machine
        nodes = InvestigationNodes(
            snapshot=active_snapshot,
            llm_client=self.llm_client,
            settings=self.settings,
        )
        graph = create_investigation_graph(nodes)

        # 5. Execute state machine
        final_state = await graph.ainvoke(initial_state)

        # 6. Extract validated report from final state
        report_data = final_state.get("final_report")
        if not report_data:
            raise IssueLensError(
                "Investigation workflow completed without generating a final report.",
                code="WORKFLOW_EXECUTION_ERROR",
                status_code=500,
            )

        report = InvestigationReport.model_validate(report_data)
        logger.info(
            "Investigation %s finished via LangGraph: status=%s, rounds=%d, tools=%d",
            active_run_id,
            report.verification_status,
            final_state.get("retrieval_round", 1),
            len(final_state.get("tool_trace", [])),
        )
        return report


def get_investigation_service(
    settings: Settings | None = None,
    github_client: GitHubClientProtocol | None = None,
    llm_client: LLMClientProtocol | None = None,
) -> InvestigationService:
    """Dependency injection factory for InvestigationService."""
    active_settings = settings or get_settings()
    gh = github_client or GitHubClient(
        token=active_settings.github_token,
        timeout_seconds=float(active_settings.request_timeout_seconds),
    )
    llm = llm_client or get_llm_client(active_settings)
    return InvestigationService(
        github_client=gh,
        llm_client=llm,
        settings=active_settings,
    )
