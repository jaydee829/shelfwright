"""chat transcript store

Revision ID: 30f1e46533e9
Revises: c804d02d6fbb
Create Date: 2026-06-09 19:14:35.030845

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "30f1e46533e9"
down_revision: str | None = "c804d02d6fbb"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    # usage.conversation_id existed since Lift 1 with no FK; pre-Stage-1 rows may hold
    # ADK session uuids that match no conversations row. NULL those orphans so the FK can
    # be added on databases that already have usage data (conversations is empty at this
    # migration, so every non-null value is an orphan).
    op.execute(
        "UPDATE usage SET conversation_id = NULL "
        "WHERE conversation_id IS NOT NULL "
        "AND conversation_id NOT IN (SELECT id FROM conversations)"
    )
    op.create_foreign_key("fk_usage_conversation_id", "usage", "conversations", ["conversation_id"], ["id"])


def downgrade() -> None:
    op.drop_constraint("fk_usage_conversation_id", "usage", type_="foreignkey")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")
