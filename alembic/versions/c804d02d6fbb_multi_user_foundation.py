"""multi-user foundation

Revision ID: c804d02d6fbb
Revises: 6c2cdc370222
Create Date: 2026-06-06 20:43:17.759624

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'c804d02d6fbb'
down_revision: Union[str, None] = '6c2cdc370222'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Must match core/user_context.DEFAULT_USER_ID — pinned here as a literal because
# migrations are frozen and must not import application code.
DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"
DEFAULT_USER_EMAIL = "jaydee829@gmail.com"


def upgrade() -> None:
    # 1. users
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("firebase_uid", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email"),
        sa.UniqueConstraint("firebase_uid"),
    )
    # 2. the default user — owner of all pre-multi-user data
    op.execute(
        sa.text(
            "INSERT INTO users (id, email, display_name, created_at) "
            "VALUES (:id, :email, 'Justin', now())"
        ).bindparams(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
    )
    # 3. user_id, nullable first so existing rows survive the ALTER
    op.add_column("reading_history", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    op.add_column("suggestions", sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True))
    # 4. backfill EVERY existing row onto the default user (the 331 read events + suggestions)
    op.execute(sa.text("UPDATE reading_history SET user_id = :id").bindparams(id=DEFAULT_USER_ID))
    op.execute(sa.text("UPDATE suggestions SET user_id = :id").bindparams(id=DEFAULT_USER_ID))
    # 5. tighten
    op.alter_column("reading_history", "user_id", nullable=False)
    op.alter_column("suggestions", "user_id", nullable=False)
    op.create_foreign_key("fk_reading_history_user_id_users", "reading_history", "users", ["user_id"], ["id"])
    op.create_foreign_key("fk_suggestions_user_id_users", "suggestions", "users", ["user_id"], ["id"])
    op.create_index("ix_reading_history_user_id", "reading_history", ["user_id"])
    op.create_index("ix_suggestions_user_id", "suggestions", ["user_id"])
    # 6. usage — one row per LLM call (ADR-048)
    op.create_table(
        "usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key_source", sa.String(), nullable=False),
        sa.Column("vendor", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_usage_user_id", "usage", ["user_id"])
    # 7. user_credentials — BYOK-ready placeholder; NO Lift 1 code path touches it
    op.create_table(
        "user_credentials",
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("vendor", sa.String(), nullable=False),
        sa.Column("encrypted_key", sa.LargeBinary(), nullable=False),
        sa.Column("kms_key_name", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("user_id", "vendor"),
    )


def downgrade() -> None:
    op.drop_table("user_credentials")
    op.drop_index("ix_usage_user_id", table_name="usage")
    op.drop_table("usage")
    op.drop_index("ix_suggestions_user_id", table_name="suggestions")
    op.drop_index("ix_reading_history_user_id", table_name="reading_history")
    op.drop_constraint("fk_suggestions_user_id_users", "suggestions", type_="foreignkey")
    op.drop_constraint("fk_reading_history_user_id_users", "reading_history", type_="foreignkey")
    op.drop_column("suggestions", "user_id")
    op.drop_column("reading_history", "user_id")
    op.drop_table("users")
