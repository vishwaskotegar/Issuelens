"""Investigation service orchestrating issue ingestion, code search, and LLM report generation."""

import json
import re
import uuid
from typing import Any

from issuelens.agent.llm import LLMClientProtocol, get_llm_client
from issuelens.agent.prompts import build_investigation_messages
from issuelens.agent.report import (
    EvidenceItem,
    InvestigationReport,
)
from issuelens.config import Settings, get_settings
from issuelens.core.errors import LLMOutputValidationError
from issuelens.core.logging import get_logger
from issuelens.github.client import GitHubClient, GitHubClientProtocol
from issuelens.github.models import GitHubIssueContext
from issuelens.github.url_parser import IssueTarget, parse_issue_target
from issuelens.repository.code_search import search_code
from issuelens.repository.snapshot import RepoSnapshot, create_snapshot_from_zip

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
    """Service orchestrating evidence-backed investigation report generation."""

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
        """Run complete investigation flow for a target issue.

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
            "Starting investigation %s for %s/%s#%d",
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
            logger.info("Downloading repository archive for %s at %s", target, context.commit_sha)
            zip_bytes = await self.github_client.download_archive(
                owner=target.owner,
                repo=target.repo,
                ref=context.commit_sha,
                max_bytes=self.settings.max_repository_bytes,
            )
            active_snapshot = create_snapshot_from_zip(
                zip_bytes=zip_bytes,
                repo_full_name=context.repository,
                commit_sha=context.commit_sha,
                max_repository_bytes=self.settings.max_repository_bytes,
                max_file_bytes=self.settings.max_file_bytes,
            )

        # 3. Deterministic lexical and AST code search
        search_query = compile_search_query(context)
        code_hits = search_code(
            query=search_query,
            repo_snapshot=active_snapshot,
            max_results=10,
        )

        # 4. Construct verified evidence items
        evidence_items: list[EvidenceItem] = [
            EvidenceItem(
                evidence_id=f"ev_{idx}",
                source_type="code",
                path_or_url=hit.path,
                start_line=hit.start_line,
                end_line=hit.end_line,
                excerpt=hit.excerpt,
                commit_sha=hit.commit_sha,
            )
            for idx, hit in enumerate(code_hits, start=1)
        ]

        logger.info(
            "Retrieved %d code evidence items for run %s",
            len(evidence_items),
            active_run_id,
        )

        # 5. Build prompt messages with prompt injection defense
        messages = build_investigation_messages(context, evidence_items)

        # 6. Request structured completion from LangChain LLM client
        payload = await self.llm_client.generate_report_payload(messages)

        # 7. Construct final report binding LLM analysis with verified snapshot provenance
        report = InvestigationReport.from_payload(
            payload=payload,
            run_id=active_run_id,
            issue_url=context.issue_url,
            repository=context.repository,
            commit_sha=context.commit_sha,
            evidence=evidence_items,
        )

        logger.info("Investigation report %s generated successfully.", active_run_id)
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
