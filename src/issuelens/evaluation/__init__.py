"""Evaluation dataset loader, benchmark cases, and metric calculation for IssueLens."""

from typing import TYPE_CHECKING, Any

from issuelens.evaluation.dataset import (
    EvaluationCaseModel,
    EvaluationDataset,
    load_evaluation_cases,
    save_evaluation_cases,
)
from issuelens.evaluation.metrics import (
    AggregatedEvaluationReport,
    CaseEvaluationMetrics,
    aggregate_evaluation_metrics,
    compute_citation_validity,
    compute_evaluation_metrics,
    compute_recall_at_k,
    compute_unsupported_claims,
    score_root_cause_alignment,
)

if TYPE_CHECKING:
    from issuelens.evaluation.run_eval import (
        EvaluationRunner,
        format_evaluation_summary,
        run_evaluation,
    )

__all__ = [
    "AggregatedEvaluationReport",
    "CaseEvaluationMetrics",
    "EvaluationCaseModel",
    "EvaluationDataset",
    "EvaluationRunner",
    "aggregate_evaluation_metrics",
    "compute_citation_validity",
    "compute_evaluation_metrics",
    "compute_recall_at_k",
    "compute_unsupported_claims",
    "format_evaluation_summary",
    "load_evaluation_cases",
    "run_evaluation",
    "save_evaluation_cases",
    "score_root_cause_alignment",
]


def __getattr__(name: str) -> Any:
    """Lazy-load runner utilities to prevent circular runpy execution warnings."""
    if name in ("EvaluationRunner", "format_evaluation_summary", "run_evaluation"):
        from issuelens.evaluation import run_eval

        return getattr(run_eval, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
