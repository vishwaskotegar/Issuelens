"""Evaluation harness runner for IssueLens retrospective benchmark."""

import argparse
import asyncio
import json
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from issuelens.agent.report import InvestigationReport
from issuelens.agent.service import InvestigationService
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
from issuelens.persistence.db import get_session_factory
from issuelens.persistence.models import (
    EvaluationCase as EvaluationCaseRecord,
)
from issuelens.persistence.models import (
    EvaluationResult as EvaluationResultRecord,
)
from issuelens.repository.snapshot import RepoSnapshot

logger = get_logger("issuelens.evaluation.runner")


class EvaluationRunner:
    """Executes evaluation benchmarks across curated cases."""

    def __init__(
        self,
        service: InvestigationService | None = None,
        snapshot_resolver: Any = None,
        session_factory: Any = None,
        model_id: str = "default_model",
        prompt_version: str = "v1.0",
        k_values: list[int] | None = None,
    ) -> None:
        self.service = service
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
                snapshot = self.snapshot_resolver(case.repo_full_name, case.target_commit_sha)
            except Exception as exc:
                logger.warning("Could not resolve snapshot for case %s: %s", case.id, exc)

        # Run investigation service
        if self.service:
            try:
                # Retrospective leak prevention: pin investigation to snapshot / target_commit_sha
                report = await self.service.investigate(
                    target_or_url=case.issue_url,
                    snapshot=snapshot,
                )
            except Exception as exc:
                logger.error("Investigation failed for case %s: %s", case.id, exc)
                error_reason = str(exc)
        else:
            error_reason = "No investigation service provided to EvaluationRunner"

        latency = time.perf_counter() - start_time

        # Compute case evaluation metrics
        metrics = compute_evaluation_metrics(
            case=case,
            report=report,
            snapshot=snapshot,
            latency_seconds=latency,
            error_reason=error_reason,
            k_values=self.k_values,
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
            "Benchmark completed: %d/%d passed, mean citation validity=%.2f, mean recall@5=%.2f",
            report.completed_cases,
            report.total_cases,
            report.mean_citation_validity,
            report.mean_recall_at_k.get(5, 0.0),
        )
        return report


def format_evaluation_summary(report: AggregatedEvaluationReport) -> str:
    """Format evaluation benchmark report into a clear console summary table."""
    cit_target = (
        "PASS" if report.benchmark_targets_met.get("citation_validity_ge_95") else "FAIL"
    )
    unsupp_target = (
        "PASS" if report.benchmark_targets_met.get("unsupported_claims_lt_0_5") else "FAIL"
    )

    lines: list[str] = [
        "=" * 78,
        "                     ISSUELENS BENCHMARK EVALUATION REPORT               ",
        "=" * 78,
        f"Total Cases:               {report.total_cases}",
        f"Completed:                 {report.completed_cases}",
        f"Failed:                    {report.failed_cases}",
        f"Insufficient Evidence:     {report.insufficient_evidence_cases}",
        f"Mean Latency:              {report.mean_latency_seconds:.2f}s",
        "-" * 78,
        "METRIC PERFORMANCE & BENCHMARK GATES:",
        f"  Citation Validity Rate:  {report.mean_citation_validity * 100:.1f}% "
        f"(Target: >= 95% -> {cit_target})",
        f"  Mean Unsupported Claims: {report.mean_unsupported_claims:.2f} per report "
        f"(Target: < 0.50 -> {unsupp_target})",
    ]

    for k, val in sorted(report.mean_recall_at_k.items()):
        gate = ""
        if k == 5:
            passed = report.benchmark_targets_met.get("recall_at_5_ge_80", False)
            gate = f" (Target: >= 80% -> {'PASS' if passed else 'FAIL'})"
        lines.append(f"  Recall@{k}:                 {val * 100:.1f}%{gate}")

    for k, val in sorted(report.mean_hit_at_k.items()):
        lines.append(f"  Hit@{k}:                    {val * 100:.1f}%")

    lines.append("-" * 78)
    lines.append("ROOT CAUSE ALIGNMENT DISTRIBUTION:")
    for cat, count in sorted(report.root_cause_distribution.items()):
        lines.append(f"  {cat.ljust(25)} {count} cases")

    if report.failure_notes:
        lines.append("-" * 78)
        lines.append("FAILURE NOTES & ANOMALIES:")
        for fn in report.failure_notes[:10]:
            lines.append(
                f"  Case {fn.get('case_id')}: status={fn.get('status')}, "
                f"cit_val={fn.get('citation_validity')}, recall@5={fn.get('recall_at_5')} "
                f"({fn.get('error_reason') or 'low citation validity'})"
            )

    lines.append("=" * 78)
    return "\n".join(lines)


async def run_evaluation(
    cases_path: Path | str = "evals/cases.jsonl",
    output_path: Path | str = "evals/results.json",
    case_id: str | None = None,
    max_cases: int | None = None,
    save_db: bool = False,
    k_values: list[int] | None = None,
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

    if runner is None:
        session_factory = get_session_factory() if save_db else None
        runner = EvaluationRunner(
            service=None,  # Standalone metric evaluator if no live service injected
            session_factory=session_factory,
            k_values=k_values,
        )

    report = await runner.run_benchmark(cases)

    # Save machine-readable results JSON
    out_file = Path(output_path)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", encoding="utf-8") as f:
        json.dump(report.model_dump(), f, indent=2, default=str)

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
        default="evals/results.json",
        help="Path to output machine-readable evaluation report JSON (default: evals/results.json)",
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
        )
    )

    print(format_evaluation_summary(report))


if __name__ == "__main__":
    main()
