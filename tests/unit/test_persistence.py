"""Unit tests for Phase 6 persistence layer: models, migrations, and repository."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from issuelens.agent.report import (
    EvidenceItem as PydanticEvidenceItem,
)
from issuelens.agent.report import (
    Hypothesis as PydanticHypothesis,
)
from issuelens.agent.report import (
    InvestigationReport,
)
from issuelens.persistence.db import (
    check_database_health,
    create_all_tables,
    normalize_database_url,
    redact_database_url,
)
from issuelens.persistence.models import (
    EvidenceItem as EvidenceItemRecord,
)
from issuelens.persistence.models import (
    Hypothesis as HypothesisRecord,
)
from issuelens.persistence.models import (
    InvestigationReportRecord,
)
from issuelens.persistence.repository import SQLAlchemyInvestigationRepository


@pytest.fixture
def sample_report() -> InvestigationReport:
    """Fixture returning a validated InvestigationReport."""
    return InvestigationReport(
        run_id="inv_test123456",
        issue_url="https://github.com/psf/requests/issues/1234",
        repository="psf/requests",
        commit_sha="abcdef1234567890abcdef1234567890abcdef12",
        summary="Test summary of the bug investigation.",
        observed_facts=["Observed 404 response on special char"],
        hypotheses=[
            PydanticHypothesis(
                title="Faulty query parameter escaping",
                explanation="Special characters are stripped before percent-encoding.",
                confidence="high",
                confidence_rationale="Reproduced in unit test",
                supporting_evidence_ids=["ev_1"],
                contradicting_evidence_ids=[],
                verification_steps=["Inspect requests/models.py:42"],
            )
        ],
        evidence=[
            PydanticEvidenceItem(
                evidence_id="ev_1",
                source_type="code",
                path_or_url="requests/models.py",
                start_line=40,
                end_line=45,
                commit_sha="abcdef1234567890abcdef1234567890abcdef12",
                excerpt="def prepare_url(url, params):",
            )
        ],
        missing_information=[],
        suggested_tests=["test_query_param_encoding()"],
        recommended_next_steps=["Patch prepare_url"],
        verification_status="verified",
        limitations=["Only tested against Python 3.12"],
    )


def test_normalize_database_url() -> None:
    """Verify URL normalization adds correct async driver prefixes."""
    assert (
        normalize_database_url("postgresql://user:pass@localhost:5432/db")
        == "postgresql+psycopg://user:pass@localhost:5432/db"
    )
    assert (
        normalize_database_url("postgres://user:pass@localhost:5432/db")
        == "postgresql+psycopg://user:pass@localhost:5432/db"
    )
    assert (
        normalize_database_url("sqlite:///data.db")
        == "sqlite+aiosqlite:///data.db"
    )
    assert (
        normalize_database_url("sqlite+aiosqlite:///data.db")
        == "sqlite+aiosqlite:///data.db"
    )


def test_redact_database_url() -> None:
    """Verify credentials in connection URLs are masked."""
    url = "postgresql+psycopg://postgres:SecretPassword123@localhost:5432/issuelens"
    redacted = redact_database_url(url)
    assert "SecretPassword123" not in redacted
    assert redacted == "postgresql+psycopg://postgres:***@localhost:5432/issuelens"


@pytest.mark.asyncio
async def test_repository_create_and_get_run() -> None:
    """Verify creating and retrieving an investigation run."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await create_all_tables(engine)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        repo = SQLAlchemyInvestigationRepository(session)
        created = await repo.create_run(
            run_id="inv_test001",
            issue_url="https://github.com/psf/requests/issues/100",
            repo_full_name="psf/requests",
            issue_number=100,
            commit_sha="1111111111111111111111111111111111111111",
            status="ingested",
            issue_title="Test Issue Title",
        )
        assert created.id == "inv_test001"
        assert created.status == "ingested"
        assert created.issue_title == "Test Issue Title"

        # Fetch and verify
        run = await repo.get_run("inv_test001")
        assert run is not None
        assert run.id == "inv_test001"
        assert run.repo_full_name == "psf/requests"
        assert run.status == "ingested"

    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_update_status() -> None:
    """Verify updating run lifecycle status."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await create_all_tables(engine)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        repo = SQLAlchemyInvestigationRepository(session)
        await repo.create_run(
            run_id="inv_test002",
            issue_url="https://github.com/psf/requests/issues/200",
            repo_full_name="psf/requests",
            issue_number=200,
            commit_sha="2222222222222222222222222222222222222222",
        )

        updated = await repo.update_run_status(
            run_id="inv_test002",
            status="running",
        )
        assert updated is not None
        assert updated.status == "running"

        failed = await repo.update_run_status(
            run_id="inv_test002",
            status="failed",
            error_code="TIMEOUT",
            error_message="Upstream API timed out",
        )
        assert failed is not None
        assert failed.status == "failed"
        assert failed.error_code == "TIMEOUT"
        assert failed.error_message == "Upstream API timed out"
        assert failed.finished_at is not None

    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_save_and_get_report(sample_report: InvestigationReport) -> None:
    """Verify persisting and retrieving a validated report with child evidence and hypotheses."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await create_all_tables(engine)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        repo = SQLAlchemyInvestigationRepository(session)
        await repo.create_run(
            run_id=sample_report.run_id,
            issue_url=sample_report.issue_url,
            repo_full_name=sample_report.repository,
            issue_number=1234,
            commit_sha=sample_report.commit_sha,
            status="running",
        )

        # Save report
        await repo.save_report(sample_report.run_id, sample_report)

        # Fetch run and verify eager-loaded relations
        run = await repo.get_run(sample_report.run_id)
        assert run is not None
        assert run.status == "completed"
        assert run.finished_at is not None
        assert len(run.evidence_items) == 1
        assert run.evidence_items[0].id == "ev_1"
        assert run.evidence_items[0].path_or_url == "requests/models.py"
        assert len(run.hypotheses) == 1
        assert run.hypotheses[0].title == "Faulty query parameter escaping"
        assert run.report is not None

        # Fetch report via get_report
        retrieved_report = await repo.get_report(sample_report.run_id)
        assert retrieved_report is not None
        assert retrieved_report.run_id == sample_report.run_id
        assert retrieved_report.summary == sample_report.summary
        assert len(retrieved_report.hypotheses) == 1
        assert len(retrieved_report.evidence) == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_repository_cascading_delete(sample_report: InvestigationReport) -> None:
    """Verify deleting a run cascades to delete evidence, hypotheses, and report."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    await create_all_tables(engine)

    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    async with session_factory() as session:
        repo = SQLAlchemyInvestigationRepository(session)
        await repo.create_run(
            run_id=sample_report.run_id,
            issue_url=sample_report.issue_url,
            repo_full_name=sample_report.repository,
            issue_number=1234,
            commit_sha=sample_report.commit_sha,
        )
        await repo.save_report(sample_report.run_id, sample_report)

        # Delete run
        deleted = await repo.delete_run(sample_report.run_id)
        assert deleted is True

        # Verify run is gone
        assert await repo.get_run(sample_report.run_id) is None
        assert await repo.get_report(sample_report.run_id) is None

        # Verify child tables are empty
        ev_items = (await session.execute(select(EvidenceItemRecord))).scalars().all()
        assert len(ev_items) == 0
        hyps = (await session.execute(select(HypothesisRecord))).scalars().all()
        assert len(hyps) == 0
        reps = (await session.execute(select(InvestigationReportRecord))).scalars().all()
        assert len(reps) == 0

    await engine.dispose()


@pytest.mark.asyncio
async def test_run_survives_restart(
    tmp_path: Path, sample_report: InvestigationReport
) -> None:
    """Acceptance Test: Verify run and report persist across process / engine restart."""
    db_file = tmp_path / "persistence_test.db"
    db_url = f"sqlite+aiosqlite:///{db_file}"

    # Step 1: Process A creates and completes the run
    engine_a = create_async_engine(db_url)
    await create_all_tables(engine_a)

    factory_a = async_sessionmaker(engine_a, expire_on_commit=False)
    async with factory_a() as session_a:
        repo_a = SQLAlchemyInvestigationRepository(session_a)
        await repo_a.create_run(
            run_id=sample_report.run_id,
            issue_url=sample_report.issue_url,
            repo_full_name=sample_report.repository,
            issue_number=1234,
            commit_sha=sample_report.commit_sha,
            status="ingested",
            issue_title="Sample Title",
        )
        await repo_a.save_report(sample_report.run_id, sample_report)
        await session_a.commit()

    # Simulate shutdown of Process A
    await engine_a.dispose()

    # Step 2: Process B starts up afresh and reconnects to the database
    engine_b = create_async_engine(db_url)
    factory_b = async_sessionmaker(engine_b, expire_on_commit=False)
    async with factory_b() as session_b:
        repo_b = SQLAlchemyInvestigationRepository(session_b)

        reloaded_run = await repo_b.get_run(sample_report.run_id)
        assert reloaded_run is not None
        assert reloaded_run.id == sample_report.run_id
        assert reloaded_run.status == "completed"
        assert reloaded_run.issue_title == "Sample Title"
        assert len(reloaded_run.evidence_items) == 1
        assert len(reloaded_run.hypotheses) == 1

        reloaded_report = await repo_b.get_report(sample_report.run_id)
        assert reloaded_report is not None
        assert reloaded_report.run_id == sample_report.run_id
        assert reloaded_report.summary == sample_report.summary
        assert reloaded_report.evidence[0].path_or_url == "requests/models.py"

    await engine_b.dispose()


@pytest.mark.asyncio
async def test_database_health_probe() -> None:
    """Verify check_database_health accurately diagnoses connectivity."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    healthy, msg = await check_database_health(engine)
    assert healthy is True
    assert msg == "ok"
    await engine.dispose()

    # Failing probe
    failing_engine = create_async_engine("sqlite+aiosqlite:////invalid_dir_9999/unreachable.db")
    failed, error_msg = await check_database_health(failing_engine)
    assert failed is False
    assert error_msg != "ok"
    await failing_engine.dispose()


def test_alembic_migrations_from_clean_database(tmp_path: Path) -> None:
    """Acceptance Test: Verify Alembic migrations apply cleanly from empty database."""
    db_file = tmp_path / "migration_clean.db"
    sync_url = f"sqlite:///{db_file}"

    alembic_cfg = Config("alembic.ini")
    alembic_cfg.set_main_option("sqlalchemy.url", sync_url)

    # Run upgrade head on clean database
    command.upgrade(alembic_cfg, "head")

    # Run downgrade base
    command.downgrade(alembic_cfg, "base")

    # Re-apply upgrade head
    command.upgrade(alembic_cfg, "head")


def test_production_requires_database_url() -> None:
    """Regression: production must not silently fall back to a local SQLite file."""
    from pydantic import SecretStr

    from issuelens.config import Settings
    from issuelens.core.errors import ConfigurationError
    from issuelens.persistence.db import get_default_database_url

    prod = Settings(
        app_env="production", auth_required=True, api_key=SecretStr("k"), database_url=None
    )
    with pytest.raises(ConfigurationError, match="DATABASE_URL"):
        get_default_database_url(prod)
