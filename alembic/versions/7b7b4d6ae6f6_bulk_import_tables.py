"""bulk import tables

Revision ID: 7b7b4d6ae6f6
Revises: 30f1e46533e9
Create Date: 2026-06-18 15:34:14.847673

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b7b4d6ae6f6"
down_revision: str | None = "30f1e46533e9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "import_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("original_filename", sa.String(), nullable=True),
        sa.Column("total_rows", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_jobs_user_id", "import_jobs", ["user_id"])
    op.create_table(
        "import_rows",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("import_job_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("raw_title", sa.String(), nullable=True),
        sa.Column("raw_author", sa.String(), nullable=True),
        sa.Column("raw_format", sa.String(), nullable=True),
        sa.Column("raw_date", sa.String(), nullable=True),
        sa.Column("date_completed", sa.Date(), nullable=True),
        sa.Column("rating", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("destination", sa.String(), nullable=False),
        sa.Column("shelf", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("outcome", sa.String(), nullable=True),
        sa.Column("skip_reason", sa.String(), nullable=True),
        sa.Column("work_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["import_job_id"], ["import_jobs.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_import_rows_import_job_id", "import_rows", ["import_job_id"])
    op.create_index("ix_import_rows_user_id", "import_rows", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_import_rows_user_id", table_name="import_rows")
    op.drop_index("ix_import_rows_import_job_id", table_name="import_rows")
    op.drop_table("import_rows")
    op.drop_index("ix_import_jobs_user_id", table_name="import_jobs")
    op.drop_table("import_jobs")
