"""Persistence package for IssueLens (SQLAlchemy models, migrations, and repository)."""

from issuelens.persistence.db import (
    check_database_health,
    close_engine,
    create_all_tables,
    drop_all_tables,
    get_db_session,
    get_default_database_url,
    get_engine,
    get_session_factory,
    init_engine,
    normalize_database_url,
    redact_database_url,
)
from issuelens.persistence.models import (
    Base,
    EvaluationCase,
    EvaluationResult,
    EvidenceItem,
    Hypothesis,
    InvestigationReportRecord,
    InvestigationRun,
)
from issuelens.persistence.repository import (
    InvestigationRepositoryProtocol,
    SQLAlchemyInvestigationRepository,
)

__all__ = [
    "Base",
    "EvaluationCase",
    "EvaluationResult",
    "EvidenceItem",
    "Hypothesis",
    "InvestigationReportRecord",
    "InvestigationRepositoryProtocol",
    "InvestigationRun",
    "SQLAlchemyInvestigationRepository",
    "check_database_health",
    "close_engine",
    "create_all_tables",
    "drop_all_tables",
    "get_db_session",
    "get_default_database_url",
    "get_engine",
    "get_session_factory",
    "init_engine",
    "normalize_database_url",
    "redact_database_url",
]

