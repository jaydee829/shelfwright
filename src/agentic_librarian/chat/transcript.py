"""User-scoped chat transcript store (Lift 2). The active thread is the current
user's most-recent conversation; New chat inserts a new row. Identity comes from
the context (get_required_user_id) — never a parameter (ADR-048)."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from agentic_librarian.core.user_context import get_required_user_id
from agentic_librarian.db.models import Conversation, Message
from agentic_librarian.db.session import DatabaseManager

db_manager = DatabaseManager()


def set_db_manager(new_manager: DatabaseManager) -> None:
    """Override the module db_manager (for tests) — the mcp/server.py pattern."""
    global db_manager
    db_manager = new_manager


@dataclass(frozen=True)
class TurnContext:
    """Everything a chat turn needs: which conversation, and its prior turns
    (oldest first) as plain {'role','content'} dicts."""

    conversation_id: UUID
    history: list[dict]


def _history(session, conversation_id: UUID) -> list[dict]:
    rows = (
        session.query(Message)
        .filter(Message.conversation_id == conversation_id)
        .order_by(Message.created_at, Message.id)  # id tiebreaks same-timestamp inserts
        .all()
    )
    return [{"role": m.role, "content": m.content} for m in rows]


def get_or_create_active_conversation() -> TurnContext:
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        conv = (
            session.query(Conversation)
            .filter(Conversation.user_id == user_id)  # scoping: my threads only
            .order_by(Conversation.created_at.desc(), Conversation.id.desc())
            .first()
        )
        if conv is None:
            conv = Conversation(user_id=user_id)
            session.add(conv)
            session.flush()
        return TurnContext(conversation_id=conv.id, history=_history(session, conv.id))


def start_new_conversation() -> TurnContext:
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        conv = Conversation(user_id=user_id)
        session.add(conv)
        session.flush()
        return TurnContext(conversation_id=conv.id, history=[])


def append_message(conversation_id: UUID, role: str, content: str) -> None:
    user_id = get_required_user_id()
    with db_manager.get_session() as session:
        # Scoping: only write into a conversation the caller owns.
        conv = (
            session.query(Conversation)
            .filter(Conversation.id == conversation_id, Conversation.user_id == user_id)
            .first()
        )
        if conv is None:
            raise PermissionError(f"conversation {conversation_id} not found for this user")
        session.add(Message(conversation_id=conversation_id, role=role, content=content))
        session.flush()
