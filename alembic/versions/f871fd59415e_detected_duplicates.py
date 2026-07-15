"""detected_duplicates — GH #141 deep-pass redirect feed for the works-merge tool

Revision ID: f871fd59415e
Revises: 48e3762d6c0c
Create Date: 2026-07-14 00:00:00.000000

Rule 11 note: this migration only ADDS a table; it alters nothing existing, so the
pre-migration-schema rehearsal pressure that applies to migration-gating tools (dedup,
requeue) is nil here — no query against an existing model changes shape.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "f871fd59415e"
down_revision: str | None = "48e3762d6c0c"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "detected_duplicates",
        sa.Column("work_id_a", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("work_id_b", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("detected_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["work_id_a"], ["works.id"]),
        sa.ForeignKeyConstraint(["work_id_b"], ["works.id"]),
        sa.PrimaryKeyConstraint("work_id_a", "work_id_b"),
    )
    op.create_index("ix_detected_duplicates_work_id_b", "detected_duplicates", ["work_id_b"])


def downgrade() -> None:
    op.drop_index("ix_detected_duplicates_work_id_b", table_name="detected_duplicates")
    op.drop_table("detected_duplicates")
