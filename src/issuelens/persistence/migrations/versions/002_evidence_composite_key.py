"""002_evidence_composite_key

Key evidence_items by (run_id, id) instead of id alone. Evidence IDs restart at
``ev_1`` for every investigation run, so the single-column key made the second
persisted report fail with a unique-constraint violation.

Revision ID: 002_evidence_composite_key
Revises: 001_initial_schema
Create Date: 2026-11-25 12:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "002_evidence_composite_key"
down_revision: str | None = "001_initial_schema"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# PostgreSQL's default name for the unnamed primary key created in 001.
_PG_DEFAULT_PK_NAME = "evidence_items_pkey"


def _evidence_table(pk_columns: list[str], pk_name: str) -> sa.Table:
    """Explicit evidence_items definition for SQLite batch rebuilds.

    Passing the target shape via ``copy_from`` avoids reflecting the old primary key
    and then fighting it (which SQLAlchemy warns will become an error).
    """
    return sa.Table(
        "evidence_items",
        sa.MetaData(),
        sa.Column("id", sa.String(length=64), nullable=False),
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
        sa.PrimaryKeyConstraint(*pk_columns, name=pk_name),
        sa.Index("ix_evidence_items_run_id", "run_id"),
    )


def upgrade() -> None:
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint(_PG_DEFAULT_PK_NAME, "evidence_items", type_="primary")
        op.create_primary_key("pk_evidence_items", "evidence_items", ["run_id", "id"])
    else:
        # SQLite cannot alter primary keys in place; batch mode rebuilds the table.
        with op.batch_alter_table(
            "evidence_items",
            recreate="always",
            copy_from=_evidence_table(["run_id", "id"], "pk_evidence_items"),
        ):
            pass


def downgrade() -> None:
    # Note: downgrading fails if two runs already share an evidence id (expected —
    # the old schema cannot represent that data).
    if op.get_bind().dialect.name == "postgresql":
        op.drop_constraint("pk_evidence_items", "evidence_items", type_="primary")
        op.create_primary_key(_PG_DEFAULT_PK_NAME, "evidence_items", ["id"])
    else:
        with op.batch_alter_table(
            "evidence_items",
            recreate="always",
            copy_from=_evidence_table(["id"], "evidence_items_pkey"),
        ):
            pass
