"""Repository layer for persisting and retrieving investigation runs, evidence, and reports."""

import hashlib
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from issuelens.agent.report import (
    InvestigationReport,
)
from issuelens.core.logging import get_logger
from issuelens.persistence.models import (
    EvidenceItem as EvidenceItemRecord,
)
from issuelens.persistence.models import (
    Hypothesis as HypothesisRecord,
)
from issuelens.persistence.models import (
    InvestigationReportRecord,
    InvestigationRun,
)

logger = get_logger("issuelens.persistence.repository")


class InvestigationRepositoryProtocol(Protocol):
    """Protocol defining the persistence interface for investigations."""

    async def create_run(
        self,
        run_id: str,
        issue_url: str,
        repo_full_name: str,
        issue_number: int,
        commit_sha: str,
        status: str = "ingested",
        issue_title: str | None = None,
        error_message: str | None = None,
    ) -> InvestigationRun:
        """Create and persist a new investigation run record."""
        ...

    async def get_run(self, run_id: str) -> InvestigationRun | None:
        """Fetch an investigation run with its report, evidence, and hypotheses."""
        ...

    async def update_run_status(
        self,
        run_id: str,
        status: str,
        finished_at: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> InvestigationRun | None:
        """Update run lifecycle status, completion timestamp, or error diagnostic."""
        ...

    async def save_report(
        self,
        run_id: str,
        report: InvestigationReport,
    ) -> InvestigationReportRecord:
        """Persist a completed investigation report, evidence items, and hypotheses."""
        ...

    async def get_report(self, run_id: str) -> InvestigationReport | None:
        """Retrieve and validate the InvestigationReport for a given run ID."""
        ...

    async def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InvestigationRun]:
        """List investigation runs ordered by creation timestamp descending."""
        ...

    async def delete_run(self, run_id: str) -> bool:
        """Delete an investigation run and all associated cascade records."""
        ...

    async def commit(self) -> None:
        """Explicitly commit the current session transaction."""
        ...


class SQLAlchemyInvestigationRepository:
    """SQLAlchemy 2.0 implementation of InvestigationRepositoryProtocol."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_run(
        self,
        run_id: str,
        issue_url: str,
        repo_full_name: str,
        issue_number: int,
        commit_sha: str,
        status: str = "ingested",
        issue_title: str | None = None,
        error_message: str | None = None,
    ) -> InvestigationRun:
        """Create and persist a new investigation run record."""
        now = datetime.now(UTC)
        run = InvestigationRun(
            id=run_id,
            issue_url=issue_url,
            repo_full_name=repo_full_name,
            issue_number=issue_number,
            issue_title=issue_title,
            commit_sha=commit_sha,
            status=status,
            started_at=now,
            created_at=now,
            updated_at=now,
            error_message=error_message,
        )
        self.session.add(run)
        await self.session.flush()
        logger.info("Persisted investigation run %s [%s]", run_id, status)
        return run

    async def get_run(self, run_id: str) -> InvestigationRun | None:
        """Fetch an investigation run with eager loading of associated records."""
        stmt = (
            select(InvestigationRun)
            .where(InvestigationRun.id == run_id)
            .options(
                selectinload(InvestigationRun.report),
                selectinload(InvestigationRun.evidence_items),
                selectinload(InvestigationRun.hypotheses),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_run_status(
        self,
        run_id: str,
        status: str,
        finished_at: datetime | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
    ) -> InvestigationRun | None:
        """Update run lifecycle status, completion timestamp, or error diagnostic."""
        run = await self.get_run(run_id)
        if run is None:
            return None

        run.status = status
        run.updated_at = datetime.now(UTC)
        if finished_at is not None:
            run.finished_at = finished_at
        elif status in ("completed", "failed") and run.finished_at is None:
            run.finished_at = datetime.now(UTC)

        if error_code is not None:
            run.error_code = error_code
        if error_message is not None:
            run.error_message = error_message

        await self.session.flush()
        logger.info("Updated investigation run %s status to %s", run_id, status)
        return run

    async def save_report(
        self,
        run_id: str,
        report: InvestigationReport,
    ) -> InvestigationReportRecord:
        """Persist a completed investigation report, evidence items, and hypotheses."""
        now = datetime.now(UTC)
        run = await self.get_run(run_id)
        if run is not None:
            run.status = "completed"
            run.finished_at = now
            run.updated_at = now

        # Upsert report record
        existing_report_stmt = select(InvestigationReportRecord).where(
            InvestigationReportRecord.run_id == run_id
        )
        existing_report_res = await self.session.execute(existing_report_stmt)
        report_record = existing_report_res.scalar_one_or_none()

        report_dict = report.model_dump()
        if report_record is not None:
            report_record.report_json = report_dict
            report_record.created_at = now
        else:
            report_record = InvestigationReportRecord(
                run_id=run_id,
                report_json=report_dict,
                schema_version="1.0",
                created_at=now,
            )
            self.session.add(report_record)

        # Clear existing evidence items and hypotheses for idempotency
        await self.session.execute(
            delete(EvidenceItemRecord).where(EvidenceItemRecord.run_id == run_id)
        )
        await self.session.execute(
            delete(HypothesisRecord).where(HypothesisRecord.run_id == run_id)
        )

        # Persist individual evidence records
        for item in report.evidence:
            content_hash = hashlib.sha256(item.excerpt.encode("utf-8")).hexdigest()
            ev_record = EvidenceItemRecord(
                id=item.evidence_id,
                run_id=run_id,
                source_type=item.source_type,
                path_or_url=item.path_or_url,
                start_line=item.start_line,
                end_line=item.end_line,
                commit_sha=item.commit_sha,
                excerpt=item.excerpt,
                content_hash=content_hash,
                created_at=now,
            )
            self.session.add(ev_record)

        # Persist hypotheses records
        for idx, hyp in enumerate(report.hypotheses):
            hyp_record = HypothesisRecord(
                id=f"hyp_{run_id}_{idx + 1}",
                run_id=run_id,
                title=hyp.title,
                explanation=hyp.explanation,
                confidence=hyp.confidence,
                support_status="supported",
                rank=idx + 1,
                supporting_evidence_ids=hyp.supporting_evidence_ids,
                contradicting_evidence_ids=hyp.contradicting_evidence_ids,
                verification_steps=hyp.verification_steps,
                created_at=now,
            )
            self.session.add(hyp_record)

        await self.session.flush()
        logger.info(
            "Persisted report for run %s with %d evidence items and %d hypotheses",
            run_id,
            len(report.evidence),
            len(report.hypotheses),
        )
        return report_record

    async def get_report(self, run_id: str) -> InvestigationReport | None:
        """Retrieve and validate the InvestigationReport for a given run ID."""
        stmt = select(InvestigationReportRecord).where(
            InvestigationReportRecord.run_id == run_id
        )
        result = await self.session.execute(stmt)
        record = result.scalar_one_or_none()
        if record is None:
            return None
        return InvestigationReport.model_validate(record.report_json)

    async def list_runs(
        self,
        limit: int = 50,
        offset: int = 0,
    ) -> list[InvestigationRun]:
        """List investigation runs ordered by creation timestamp descending."""
        stmt = (
            select(InvestigationRun)
            .order_by(desc(InvestigationRun.created_at))
            .limit(limit)
            .offset(offset)
            .options(
                selectinload(InvestigationRun.report),
                selectinload(InvestigationRun.evidence_items),
                selectinload(InvestigationRun.hypotheses),
            )
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def delete_run(self, run_id: str) -> bool:
        """Delete an investigation run and all associated cascade records."""
        run = await self.get_run(run_id)
        if run is None:
            return False
        await self.session.delete(run)
        await self.session.flush()
        return True

    async def commit(self) -> None:
        """Explicitly commit the current session transaction."""
        await self.session.commit()
