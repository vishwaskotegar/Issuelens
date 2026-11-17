"""Evaluation dataset loader, benchmark cases, and metric calculation for IssueLens."""

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

__all__ = [
    "AggregatedEvaluationReport",
    "CaseEvaluationMetrics",
    "EvaluationCaseModel",
    "EvaluationDataset",
    "aggregate_evaluation_metrics",
    "compute_citation_validity",
    "compute_evaluation_metrics",
    "compute_recall_at_k",
    "compute_unsupported_claims",
    "load_evaluation_cases",
    "save_evaluation_cases",
    "score_root_cause_alignment",
]
