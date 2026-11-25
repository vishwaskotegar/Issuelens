"""SQLAlchemy 2.0 ORM models for IssueLens persistence."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, PrimaryKeyConstraint, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    """Base class for all IssueLens SQLAlchemy models."""

    type_annotation_map = {
        dict[str, Any]: JSON,
        list[str]: JSON,
        list[dict[str, Any]]: JSON,
    }


class InvestigationRun(Base):
    """Tracks the lifecycle, provenance, and execution state of an investigation."""

    __tablename__ = "investigation_runs"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issue_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    issue_number: Mapped[int] = mapped_column(Integer, nullable=False)
    issue_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="ingested", index=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )

    # Relationships
    evidence_items: Mapped[list["EvidenceItem"]] = relationship(
        "EvidenceItem",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="EvidenceItem.id",
    )
    hypotheses: Mapped[list["Hypothesis"]] = relationship(
        "Hypothesis",
        back_populates="run",
        cascade="all, delete-orphan",
        order_by="Hypothesis.rank",
    )
    report: Mapped["InvestigationReportRecord | None"] = relationship(
        "InvestigationReportRecord",
        back_populates="run",
        uselist=False,
        cascade="all, delete-orphan",
    )


class EvidenceItem(Base):
    """Persisted source evidence cited during an investigation."""

    __tablename__ = "evidence_items"
    # Evidence IDs (ev_1, doc_ev_1, ...) are only unique within a run, so the key is
    # composite. A single-column key on ``id`` made the second persisted run collide.
    __table_args__ = (PrimaryKeyConstraint("run_id", "id", name="pk_evidence_items"),)

    id: Mapped[str] = mapped_column(String(64))
    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("investigation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    path_or_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    start_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(40), nullable=True)
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # Relationship
    run: Mapped["InvestigationRun"] = relationship(
        "InvestigationRun", back_populates="evidence_items"
    )


class Hypothesis(Base):
    """Persisted hypothesis linked to an investigation run."""

    __tablename__ = "hypotheses"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("investigation_runs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)
    support_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="untested"
    )
    rank: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    supporting_evidence_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    contradicting_evidence_ids: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    verification_steps: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # Relationship
    run: Mapped["InvestigationRun"] = relationship("InvestigationRun", back_populates="hypotheses")


class InvestigationReportRecord(Base):
    """Persisted validated investigation report."""

    __tablename__ = "investigation_reports"

    run_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("investigation_runs.id", ondelete="CASCADE"),
        primary_key=True,
    )
    report_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    schema_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="1.0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # Relationship
    run: Mapped["InvestigationRun"] = relationship("InvestigationRun", back_populates="report")


class EvaluationCase(Base):
    """Curated benchmark evaluation test case."""

    __tablename__ = "evaluation_cases"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    issue_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    repo_full_name: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    target_commit_sha: Mapped[str] = mapped_column(String(40), nullable=False)
    known_resolution: Mapped[str | None] = mapped_column(Text, nullable=True)
    expected_files: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # Relationship
    results: Mapped[list["EvaluationResult"]] = relationship(
        "EvaluationResult",
        back_populates="case",
        cascade="all, delete-orphan",
    )


class EvaluationResult(Base):
    """Results from running evaluation harness on a case."""

    __tablename__ = "evaluation_results"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    case_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("evaluation_cases.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    model_id: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(64), nullable=False)
    metrics_json: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False
    )
    report_json: Mapped[dict[str, Any] | None] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(UTC),
    )

    # Relationship
    case: Mapped["EvaluationCase"] = relationship("EvaluationCase", back_populates="results")
