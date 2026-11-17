"""001_initial_schema

Revision ID: 001_initial_schema
Revises: None
Create Date: 2026-11-17 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "001_initial_schema"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # 1. investigation_runs
    op.create_table(
        "investigation_runs",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("issue_url", sa.String(length=1024), nullable=False),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column("issue_number", sa.Integer(), nullable=False),
        sa.Column("issue_title", sa.String(length=512), nullable=True),
        sa.Column("commit_sha", sa.String(length=40), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False, server_default="ingested"),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_investigation_runs_repo_full_name", "investigation_runs", ["repo_full_name"]
    )
    op.create_index("ix_investigation_runs_status", "investigation_runs", ["status"])

    # 2. evidence_items
    op.create_table(
        "evidence_items",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("investigation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("path_or_url", sa.String(length=1024), nullable=False),
        sa.Column("start_line", sa.Integer(), nullable=True),
        sa.Column("end_line", sa.Integer(), nullable=True),
        sa.Column("commit_sha", sa.String(length=40), nullable=True),
        sa.Column("excerpt", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evidence_items_run_id", "evidence_items", ["run_id"])

    # 3. hypotheses
    op.create_table(
        "hypotheses",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("investigation_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=512), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("confidence", sa.String(length=32), nullable=False),
        sa.Column(
            "support_status", sa.String(length=32), nullable=False, server_default="untested"
        ),
        sa.Column("rank", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "supporting_evidence_ids",
            sa.JSON().with_variant(JSONB, "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "contradicting_evidence_ids",
            sa.JSON().with_variant(JSONB, "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "verification_steps",
            sa.JSON().with_variant(JSONB, "postgresql"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_hypotheses_run_id", "hypotheses", ["run_id"])

    # 4. investigation_reports
    op.create_table(
        "investigation_reports",
        sa.Column(
            "run_id",
            sa.String(length=64),
            sa.ForeignKey("investigation_runs.id", ondelete="CASCADE"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "report_json",
            sa.JSON().with_variant(JSONB, "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "schema_version",
            sa.String(length=32),
            nullable=False,
            server_default="1.0",
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    # 5. evaluation_cases
    op.create_table(
        "evaluation_cases",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column("issue_url", sa.String(length=1024), nullable=False),
        sa.Column("repo_full_name", sa.String(length=255), nullable=False),
        sa.Column("target_commit_sha", sa.String(length=40), nullable=False),
        sa.Column("known_resolution", sa.Text(), nullable=True),
        sa.Column(
            "expected_files",
            sa.JSON().with_variant(JSONB, "postgresql"),
            nullable=False,
        ),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_evaluation_cases_repo_full_name", "evaluation_cases", ["repo_full_name"]
    )

    # 6. evaluation_results
    op.create_table(
        "evaluation_results",
        sa.Column("id", sa.String(length=64), primary_key=True, nullable=False),
        sa.Column(
            "case_id",
            sa.String(length=64),
            sa.ForeignKey("evaluation_cases.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("prompt_version", sa.String(length=64), nullable=False),
        sa.Column(
            "metrics_json",
            sa.JSON().with_variant(JSONB, "postgresql"),
            nullable=False,
        ),
        sa.Column(
            "report_json",
            sa.JSON().with_variant(JSONB, "postgresql"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_evaluation_results_case_id", "evaluation_results", ["case_id"])


def downgrade() -> None:
    op.drop_table("evaluation_results")
    op.drop_table("evaluation_cases")
    op.drop_table("investigation_reports")
    op.drop_table("hypotheses")
    op.drop_table("evidence_items")
    op.drop_table("investigation_runs")
