"""Evaluation harness runner for IssueLens retrospective benchmark."""

import argparse
import asyncio
import inspect
import json
import subprocess
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from issuelens.agent.llm import get_llm_client
from issuelens.agent.report import EvidenceItem, Hypothesis, InvestigationReport
from issuelens.agent.service import InvestigationService
from issuelens.config import get_settings
from issuelens.core.errors import diagnostic_error_message
from issuelens.core.logging import get_logger
from issuelens.evaluation.dataset import (
    EvaluationCaseModel,
    EvaluationDataset,
)
from issuelens.evaluation.metrics import (
    AggregatedEvaluationReport,
    CaseEvaluationMetrics,
    aggregate_evaluation_metrics,
    compute_evaluation_metrics,
)
from issuelens.github.client import GitHubClient
from issuelens.github.models import (
    GitHubComment,
    GitHubIssue,
    GitHubIssueContext,
    GitHubRepo,
)
from issuelens.github.url_parser import IssueTarget
from issuelens.persistence.db import get_session_factory
from issuelens.persistence.models import (
    EvaluationCase as EvaluationCaseRecord,
)
from issuelens.persistence.models import (
    EvaluationResult as EvaluationResultRecord,
)
from issuelens.repository.snapshot import (
    RepoSnapshot,
    cache_and_create_snapshot_from_zip,
    get_cached_snapshot,
)
from issuelens.repository.snippets import read_snippet

logger = get_logger("issuelens.evaluation.runner")


class EvaluationRunner:
    """Executes evaluation benchmarks across curated cases."""

    def __init__(
        self,
        service: Any = None,
        snapshot_resolver: Any = None,
        session_factory: Any = None,
        model_id: str = "default_model",
        prompt_version: str = "v1.0",
        k_values: list[int] | None = None,
        require_snapshot: bool = False,
    ) -> None:
        self.service = service
        # Live benchmarks must never fall back to another commit: if the pinned pre-fix
        # snapshot cannot be loaded, the case fails instead of silently investigating
        # whatever the default branch (possibly containing the fix) looks like now.
        self.require_snapshot = require_snapshot
        self.snapshot_resolver = snapshot_resolver
        self.session_factory = session_factory
        self.model_id = model_id
        self.prompt_version = prompt_version
        self.k_values = k_values or [1, 3, 5, 10]

    async def evaluate_case(
        self,
        case: EvaluationCaseModel,
    ) -> tuple[CaseEvaluationMetrics, InvestigationReport | None]:
        """Run investigation on a single case and compute all evaluation metrics."""
        logger.info(
            "Evaluating case %s (%s#%d, pre-fix SHA: %s)",
            case.id,
            case.repo_full_name,
            case.issue_number,
            case.target_commit_sha[:8],
        )

        start_time = time.perf_counter()
        report: InvestigationReport | None = None
        error_reason: str | None = None
        snapshot: RepoSnapshot | None = None

        # Resolve pinned pre-fix snapshot if resolver is provided
        if self.snapshot_resolver:
            try:
                res = self.snapshot_resolver(case.repo_full_name, case.target_commit_sha)
                if inspect.isawaitable(res):
                    snapshot = await res
                else:
                    snapshot = res
            except Exception as exc:
                logger.warning("Could not resolve snapshot for case %s: %s", case.id, exc)

        failure_status = "failed"
        if self.require_snapshot and snapshot is None:
            failure_status = "snapshot_unavailable"
            error_reason = (
                f"Pinned pre-fix snapshot {case.target_commit_sha} could not be loaded; "
                "case not run (no fallback to another commit)."
            )
            logger.error("Case %s skipped: %s", case.id, error_reason)
        elif self.service:
            try:
                # Retrospective leak prevention: pin to the pre-fix snapshot and commit.
                report = await self.service.investigate(
                    target_or_url=case.issue_url,
                    snapshot=snapshot,
                    commit_sha=case.target_commit_sha,
                )
            except Exception as exc:
                logger.error("Investigation failed for case %s: %s", case.id, exc)
                # Results files are committed: never write raw secrets into them.
                error_reason = diagnostic_error_message(exc)
        else:
            error_reason = "No investigation service provided to EvaluationRunner"

        latency = time.perf_counter() - start_time

        # Compute case evaluation metrics
        metrics = compute_evaluation_metrics(
            case=case,
            report=report,
            latency_seconds=latency,
            error_reason=error_reason,
            k_values=self.k_values,
            failure_status=failure_status,
        )

        # Optionally persist evaluation case & result to database
        if self.session_factory:
            try:
                await self._persist_case_result(case, metrics, report)
            except Exception as exc:
                logger.warning("Failed to persist evaluation record for %s: %s", case.id, exc)

        return metrics, report

    async def _persist_case_result(
        self,
        case: EvaluationCaseModel,
        metrics: CaseEvaluationMetrics,
        report: InvestigationReport | None,
    ) -> None:
        """Persist EvaluationCase and EvaluationResult records into database."""
        async with self.session_factory() as session, session.begin():
            # Merge or insert evaluation case
            case_record = await session.get(EvaluationCaseRecord, case.id)
            if not case_record:
                case_record = EvaluationCaseRecord(
                    id=case.id,
                    issue_url=case.issue_url,
                    repo_full_name=case.repo_full_name,
                    target_commit_sha=case.target_commit_sha,
                    known_resolution=case.known_resolution,
                    expected_files=case.expected_files,
                    notes=case.notes,
                    created_at=datetime.now(UTC),
                )
                session.add(case_record)
            else:
                case_record.known_resolution = case.known_resolution
                case_record.expected_files = case.expected_files

            # Create evaluation result
            result_record = EvaluationResultRecord(
                id=str(uuid.uuid4()),
                case_id=case.id,
                model_id=self.model_id,
                prompt_version=self.prompt_version,
                metrics_json=metrics.model_dump(),
                report_json=report.model_dump() if report else None,
                created_at=datetime.now(UTC),
            )
            session.add(result_record)

    async def run_benchmark(
        self,
        cases: list[EvaluationCaseModel],
    ) -> AggregatedEvaluationReport:
        """Run evaluation benchmark across a collection of cases."""
        logger.info("Starting evaluation benchmark on %d cases", len(cases))
        metrics_list: list[CaseEvaluationMetrics] = []

        for case in cases:
            metrics, _ = await self.evaluate_case(case)
            metrics_list.append(metrics)

        report = aggregate_evaluation_metrics(metrics_list, k_values=self.k_values)
        logger.info(
            "Benchmark completed: %d/%d scored, root-cause useful rate=%.2f, "
            "search recall@5=%.2f",
            report.scored_cases,
            report.total_cases,
            report.root_cause_useful_rate,
            report.mean_search_recall_at_k.get(5, 0.0),
        )
        return report


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def format_evaluation_summary(report: AggregatedEvaluationReport) -> str:
    """Format evaluation benchmark report into a console summary table."""
    gates = report.benchmark_targets_met

    def gate(key: str) -> str:
        return "PASS" if gates.get(key) else "FAIL"

    lines: list[str] = [
        "=" * 78,
        "                     ISSUELENS BENCHMARK EVALUATION REPORT               ",
        "=" * 78,
        f"Total Cases:               {report.total_cases}",
        f"Scored (produced report):  {report.scored_cases} ({_pct(report.completion_rate)})",
        f"  Supported diagnosis:     {report.completed_cases}",
        f"  Insufficient evidence:   {report.insufficient_evidence_cases}",
        f"Failed / no snapshot:      {report.failed_cases}",
        f"Mean Latency:              {report.mean_latency_seconds:.2f}s",
        "-" * 78,
        "QUALITY (means over scored cases):",
        f"  Root cause useful rate:  {_pct(report.root_cause_useful_rate)} "
        f"(Target: >= 70% -> {gate('root_cause_useful_ge_70')})",
        f"  LLM citation validity:   {_pct(report.mean_citation_validity)} over "
        f"{report.cases_with_citations} cases with citations "
        f"(Target: >= 95% -> {gate('citation_validity_ge_95')})",
        f"  Unsupported claims:      {report.mean_unsupported_claims:.2f} per report "
        f"(Target: < 0.50 -> {gate('unsupported_claims_lt_0_5')})",
    ]
    for k, val in sorted(report.mean_diagnosis_hit_at_k.items()):
        lines.append(f"  Diagnosis Hit@{k} (cited files):   {_pct(val)}")
    lines.append("RETRIEVAL (search tool ranking, not the diagnosis):")
    for k, val in sorted(report.mean_search_recall_at_k.items()):
        suffix = (
            f" (Target: >= 80% -> {gate('search_recall_at_5_ge_80')})" if k == 5 else ""
        )
        lines.append(f"  Search Recall@{k}:        {_pct(val)}{suffix}")
    for k, val in sorted(report.mean_search_hit_at_k.items()):
        suffix = f" (Target: >= 90% -> {gate('search_hit_at_5_ge_90')})" if k == 5 else ""
        lines.append(f"  Search Hit@{k}:           {_pct(val)}{suffix}")
    lines.append("DIAGNOSTIC (not a quality metric):")
    lines.append(
        f"  Fix-title keyword overlap: agent {report.mean_keyword_overlap:.2f} vs "
        f"issue-text echo baseline {report.mean_issue_echo_keyword_overlap:.2f}"
    )

    lines.append("-" * 78)
    lines.append("ROOT CAUSE ALIGNMENT DISTRIBUTION (file-based):")
    for cat, count in sorted(report.root_cause_distribution.items()):
        lines.append(f"  {cat.ljust(25)} {count} cases")

    if report.failure_notes:
        lines.append("-" * 78)
        lines.append("FAILURE NOTES & ANOMALIES:")
        for fn in report.failure_notes[:10]:
            lines.append(
                f"  Case {fn.get('case_id')}: status={fn.get('status')}, "
                f"alignment={fn.get('root_cause_alignment')} "
                f"({fn.get('error_reason') or 'see case metrics'})"
            )

    lines.append("=" * 78)
    return "\n".join(lines)


class EvaluationGitHubClient:
    """Delegates to GitHubClient, providing local fixture issue context for sample/repo."""

    def __init__(
        self,
        real_client: GitHubClient,
        cases: list[EvaluationCaseModel] | None = None,
    ) -> None:
        self._real = real_client
        self._cases = {
            (c.repo_full_name.lower(), c.issue_number): c for c in (cases or [])
        }

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> GitHubIssue:
        if owner == "sample" and repo == "repo":
            return GitHubIssue(
                number=issue_number,
                title="Empty token accepted in TokenAuthenticator",
                body=(
                    "TokenAuthenticator does not raise an error when an empty string "
                    "token is passed to validate_token."
                ),
                state="open",
                author="tester",
                labels=["bug"],
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                comments_count=0,
                html_url=f"https://github.com/sample/repo/issues/{issue_number}",
            )
        return await self._real.get_issue(owner, repo, issue_number)

    async def get_comments(
        self, owner: str, repo: str, issue_number: int, max_comments: int | None = None
    ) -> list[GitHubComment]:
        if owner == "sample" and repo == "repo":
            return []
        return await self._real.get_comments(owner, repo, issue_number, max_comments)

    async def get_repository(self, owner: str, repo: str) -> GitHubRepo:
        if owner == "sample" and repo == "repo":
            return GitHubRepo(
                owner="sample",
                name="repo",
                full_name="sample/repo",
                default_branch="main",
                html_url="https://github.com/sample/repo",
                description="Local fixture repository",
            )
        return await self._real.get_repository(owner, repo)

    async def get_pinned_commit_sha(self, owner: str, repo: str, ref: str) -> str:
        if owner == "sample" and repo == "repo":
            return "0123456789abcdef0123456789abcdef01234567"
        return await self._real.get_pinned_commit_sha(owner, repo, ref)

    async def download_archive(
        self, owner: str, repo: str, ref: str, max_bytes: int = 50_000_000
    ) -> bytes:
        if owner == "sample" and repo == "repo":
            return b""
        return await self._real.download_archive(owner, repo, ref, max_bytes)

    async def fetch_issue_context(self, target: IssueTarget) -> GitHubIssueContext:
        if target.owner == "sample" and target.repo == "repo":
            issue = await self.get_issue(target.owner, target.repo, target.issue_number)
            return GitHubIssueContext(
                issue_url=target.canonical_url,
                repository="sample/repo",
                issue_number=target.issue_number,
                default_branch="main",
                commit_sha="0123456789abcdef0123456789abcdef01234567",
                issue=issue,
                comments=[],
                fetched_at=datetime.now(UTC),
            )
        context = await self._real.fetch_issue_context(target)
        return self.withhold_post_report_information(context, target)

    def withhold_post_report_information(
        self, context: GitHubIssueContext, target: IssueTarget
    ) -> GitHubIssueContext:
        """Remove information that may reveal the fix from a live issue context.

        * Comments are withheld: maintainers often discuss or link the fix there.
        * The issue title/body are replaced by the copy frozen in ``cases.jsonl`` when
          available (the live text may have been edited after the fix).
        * The issue is presented as open; ``closed`` tells the model a fix exists.
        * The commit is the case's pinned pre-fix SHA.
        """
        case = self._cases.get((target.repo_full_name.lower(), target.issue_number))
        issue_updates: dict[str, Any] = {
            "state": "open",
            "closed_at": None,
            "comments_count": 0,
        }
        context_updates: dict[str, Any] = {"comments": []}
        if case is not None:
            if case.issue_title:
                issue_updates["title"] = case.issue_title
            if case.issue_body is not None:
                issue_updates["body"] = case.issue_body
            context_updates["commit_sha"] = case.target_commit_sha
        context_updates["issue"] = context.issue.model_copy(update=issue_updates)
        return context.model_copy(update=context_updates)


def create_fixture_snapshot_resolver() -> Any:
    """Resolve snapshots for local fixture repositories like sample/repo."""

    def _resolve(repo_full_name: str, target_commit_sha: str) -> RepoSnapshot | None:
        if repo_full_name == "sample/repo":
            fixture_path = Path("tests/fixtures/sample_repo").resolve()
            if fixture_path.is_dir():
                return RepoSnapshot(
                    root_path=fixture_path,
                    repo_full_name="sample/repo",
                    commit_sha=target_commit_sha,
                )
        return None

    return _resolve


def create_live_snapshot_resolver(
    github_client: Any,
    settings: Any,
) -> Any:
    """Resolve repository snapshot for a case, downloading and caching if necessary."""

    async def _resolve(repo_full_name: str, target_commit_sha: str) -> RepoSnapshot | None:
        if repo_full_name == "sample/repo":
            fixture_path = Path("tests/fixtures/sample_repo").resolve()
            if fixture_path.is_dir():
                return RepoSnapshot(
                    root_path=fixture_path,
                    repo_full_name="sample/repo",
                    commit_sha=target_commit_sha,
                )

        owner, repo = (
            repo_full_name.split("/", 1)
            if "/" in repo_full_name
            else (repo_full_name, repo_full_name)
        )
        cache_dir = Path(settings.snapshot_cache_dir)
        cached = get_cached_snapshot(
            cache_dir=cache_dir,
            owner=owner,
            repo=repo,
            commit_sha=target_commit_sha,
            max_file_bytes=settings.max_file_bytes,
        )
        if cached is not None:
            return cached

        # Download archive from GitHub pinned to target_commit_sha
        try:
            zip_bytes = await github_client.download_archive(
                owner=owner,
                repo=repo,
                ref=target_commit_sha,
                max_bytes=settings.max_repository_bytes,
            )
            return cache_and_create_snapshot_from_zip(
                cache_dir=cache_dir,
                owner=owner,
                repo=repo,
                commit_sha=target_commit_sha,
                zip_bytes=zip_bytes,
                max_repository_bytes=settings.max_repository_bytes,
                max_file_bytes=settings.max_file_bytes,
                max_cached_snapshots=max(30, settings.max_cached_snapshots),
                max_cache_total_bytes=settings.max_cache_total_bytes,
            )
        except Exception as exc:
            logger.warning(
                "Could not download snapshot for %s at %s: %s",
                repo_full_name,
                target_commit_sha,
                exc,
            )
            return None

    return _resolve


class MockInvestigationService:
    """Deterministic mock investigation service for hermetic benchmark verification."""

    def __init__(self, dataset: EvaluationDataset | None = None) -> None:
        self.dataset = dataset

    async def investigate(
        self,
        target_or_url: str | Any,
        snapshot: RepoSnapshot | None = None,
        run_id: str | None = None,
        commit_sha: str | None = None,
    ) -> InvestigationReport:
        """Return a report built from the case's own ground truth.

        PLUMBING ONLY: this echoes ``expected_files`` and ``known_resolution`` back, so
        every quality metric is perfect by construction. It verifies that the harness,
        metrics and serialization run end to end; it says nothing about agent quality.
        """
        active_run_id = run_id or f"inv_mock_{uuid.uuid4().hex[:8]}"
        url_str = str(target_or_url)

        matched_case: EvaluationCaseModel | None = None
        if self.dataset:
            for c in self.dataset.cases:
                if c.issue_url == url_str or f"#{c.issue_number}" in url_str:
                    matched_case = c
                    break

        if matched_case:
            repo_name = matched_case.repo_full_name
            commit_sha = matched_case.target_commit_sha
            resolution = matched_case.known_resolution
            files = matched_case.expected_files or ["sample_pkg/auth.py"]
        else:
            repo_name = "sample/repo"
            commit_sha = (
                snapshot.commit_sha
                if snapshot
                else "0123456789abcdef0123456789abcdef01234567"
            )
            resolution = "Fixed token validation to reject empty tokens."
            files = ["sample_pkg/auth.py"]

        evidence_items: list[EvidenceItem] = []
        for idx, file_path in enumerate(files, start=1):
            start_line = 1
            end_line = 5
            excerpt = f"// Code excerpt from {file_path}"
            if snapshot and (snapshot.root_path / file_path).is_file():
                try:
                    snippet = read_snippet(snapshot, file_path, 1, 5)
                    excerpt = snippet
                    start_line = 1
                    end_line = max(1, len(snippet.splitlines()))
                except Exception:
                    pass

            evidence_items.append(
                EvidenceItem(
                    evidence_id=f"ev_{idx}",
                    source_type="code",
                    path_or_url=file_path,
                    start_line=start_line,
                    end_line=end_line,
                    commit_sha=commit_sha,
                    excerpt=excerpt,
                )
            )

        hypotheses = [
            Hypothesis(
                title=f"Root cause in {files[0]}",
                explanation=resolution,
                confidence="high",
                supporting_evidence_ids=[ev.evidence_id for ev in evidence_items],
            )
        ]

        return InvestigationReport(
            run_id=active_run_id,
            issue_url=url_str,
            repository=repo_name,
            commit_sha=commit_sha,
            summary=f"Automated benchmark diagnosis: {resolution}",
            observed_facts=["Observed bug symptoms matching benchmark report."],
            hypotheses=hypotheses,
            evidence=evidence_items,
            missing_information=[],
            suggested_tests=["tests/test_auth.py"],
            recommended_next_steps=["Verify fix against reproduction test."],
            verification_status="verified",
            limitations=[],
        )


def _get_git_commit_sha() -> str:
    """Retrieve IssueLens repository HEAD commit SHA for provenance recording."""
    try:
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, check=True
        )
        return res.stdout.strip()
    except Exception:
        return "unknown"


def _git_tree_is_dirty() -> bool | None:
    """True if tracked files differ from HEAD (results would not be reproducible from SHA)."""
    try:
        res = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            capture_output=True,
            text=True,
            check=True,
        )
        return bool(res.stdout.strip())
    except Exception:
        return None


DEFAULT_LIVE_OUTPUT = "evals/results.json"
DEFAULT_MOCK_OUTPUT = "evals/results.mock.json"
MOCK_MODE_WARNING = (
    "Mock mode echoes each case's ground truth back as the report. Its metrics verify the "
    "harness only and are NOT a measurement of investigation quality."
)


async def run_evaluation(
    cases_path: Path | str = "evals/cases.jsonl",
    output_path: Path | str | None = None,
    case_id: str | None = None,
    max_cases: int | None = None,
    save_db: bool = False,
    k_values: list[int] | None = None,
    live: bool = False,
    runner: EvaluationRunner | None = None,
) -> AggregatedEvaluationReport:
    """Load benchmark cases, run evaluation harness, and persist results."""
    cases_file = Path(cases_path)
    dataset = EvaluationDataset.from_jsonl(cases_file)
    cases = dataset.cases

    if case_id:
        target = dataset.get(case_id)
        if not target:
            raise ValueError(f"Case '{case_id}' not found in {cases_path}")
        cases = [target]

    if max_cases and max_cases > 0:
        cases = cases[:max_cases]

    owned_client: GitHubClient | None = None
    if runner is None:
        session_factory = get_session_factory() if save_db else None
        if live:
            settings = get_settings()
            raw_client = GitHubClient(
                token=settings.github_token,
                timeout_seconds=float(settings.request_timeout_seconds),
            )
            owned_client = raw_client
            github_client: Any = EvaluationGitHubClient(raw_client, cases=dataset.cases)
            llm_client = get_llm_client(settings)
            service: Any = InvestigationService(
                github_client=github_client,
                llm_client=llm_client,
                settings=settings,
            )
            snapshot_resolver = create_live_snapshot_resolver(
                github_client=github_client,
                settings=settings,
            )
            runner = EvaluationRunner(
                service=service,
                snapshot_resolver=snapshot_resolver,
                session_factory=session_factory,
                model_id=settings.llm_model or "default_model",
                prompt_version="v1.0",
                k_values=k_values,
                require_snapshot=True,
            )
        else:
            # Hermetic mock runner for offline evaluation and benchmark verification
            mock_service: Any = MockInvestigationService(dataset=dataset)
            snapshot_resolver = create_fixture_snapshot_resolver()
            runner = EvaluationRunner(
                service=mock_service,
                snapshot_resolver=snapshot_resolver,
                session_factory=session_factory,
                model_id="mock-benchmark-engine",
                prompt_version="v1.0",
                k_values=k_values,
            )

    try:
        report = await runner.run_benchmark(cases)
    finally:
        if owned_client is not None:
            await owned_client.close()

    # Save machine-readable results JSON with provenance metadata. Mock results go to
    # a separate file so they can never overwrite a recorded live benchmark.
    out_file = Path(output_path or (DEFAULT_LIVE_OUTPUT if live else DEFAULT_MOCK_OUTPUT))
    out_file.parent.mkdir(parents=True, exist_ok=True)
    report_dict = report.model_dump()
    result_data: dict[str, Any] = {
        "mode": "live" if live else "mock",
        "run_timestamp": datetime.now(UTC).isoformat(),
        "model_name": runner.model_id,
        "issuelens_commit_sha": _get_git_commit_sha(),
        "issuelens_tree_dirty": _git_tree_is_dirty(),
        "metrics_version": "2",
    }
    if live:
        settings = get_settings()
        result_data["config"] = {
            "llm_provider": settings.llm_provider,
            "llm_model": settings.llm_model,
            "max_retrieval_rounds": settings.max_retrieval_rounds,
            "llm_timeout_seconds": settings.llm_timeout_seconds,
            "max_report_tokens": settings.max_report_tokens,
        }
    else:
        result_data["warning"] = MOCK_MODE_WARNING
    result_data.update(report_dict)
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(result_data, f, indent=2, default=str)

    logger.info("Saved evaluation results to %s", out_file)
    return report


def main() -> None:
    """CLI entrypoint for evaluation harness."""
    import sys

    if sys.platform == "win32":
        try:
            from asyncio import WindowsSelectorEventLoopPolicy

            if not isinstance(
                asyncio.get_event_loop_policy(), WindowsSelectorEventLoopPolicy
            ):
                asyncio.set_event_loop_policy(WindowsSelectorEventLoopPolicy())
        except Exception:
            pass

    parser = argparse.ArgumentParser(
        description="IssueLens Evaluation Benchmark Harness"
    )
    parser.add_argument(
        "--cases-path",
        default="evals/cases.jsonl",
        help="Path to evaluation cases JSON Lines file (default: evals/cases.jsonl)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help=(
            f"Output JSON path (default: {DEFAULT_LIVE_OUTPUT} with --live, "
            f"{DEFAULT_MOCK_OUTPUT} otherwise)"
        ),
    )
    parser.add_argument(
        "--case-id",
        default=None,
        help="Optional specific case ID to evaluate (e.g., CASE-001)",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=None,
        help="Limit number of cases to evaluate",
    )
    parser.add_argument(
        "--save-db",
        action="store_true",
        help="Persist evaluation cases and results to PostgreSQL / SQLite database",
    )
    parser.add_argument(
        "--k-values",
        default="1,3,5,10",
        help="Comma-separated ranks for Recall@k calculation (default: 1,3,5,10)",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Execute live investigations against real LLM and GitHub APIs (requires credentials)",
    )

    args = parser.parse_args()
    k_vals = [int(x.strip()) for x in args.k_values.split(",") if x.strip()]

    report = asyncio.run(
        run_evaluation(
            cases_path=args.cases_path,
            output_path=args.output,
            case_id=args.case_id,
            max_cases=args.max_cases,
            save_db=args.save_db,
            k_values=k_vals,
            live=args.live,
        )
    )

    print(format_evaluation_summary(report))
    if not args.live:
        print(f"NOTE: {MOCK_MODE_WARNING}")


if __name__ == "__main__":
    main()
