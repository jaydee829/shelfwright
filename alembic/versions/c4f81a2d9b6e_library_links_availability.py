"""library links + availability

Revision ID: c4f81a2d9b6e
Revises: 7b7b4d6ae6f6
Create Date: 2026-06-25 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c4f81a2d9b6e"
down_revision: str | None = "7b7b4d6ae6f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_libraries",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("library_slug", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "provider", "library_slug"),
    )
    op.create_table(
        "availability_cache",
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("library_slug", sa.String(), nullable=False),
        sa.Column("norm_title", sa.String(), nullable=False),
        sa.Column("norm_author", sa.String(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("provider", "library_slug", "norm_title", "norm_author"),
    )


def downgrade() -> None:
    op.drop_table("availability_cache")
    op.drop_table("user_libraries")
