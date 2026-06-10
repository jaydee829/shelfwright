from uuid import UUID

import pytest
from sqlalchemy import inspect

from agentic_librarian.chat import transcript
from agentic_librarian.core.user_context import as_user
from agentic_librarian.db.models import User
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration

OTHER_USER = UUID("00000000-0000-4000-8000-0000000000ff")


@pytest.fixture
def store_db(db_url):
    """Point the transcript module at the isolated test DB (test_usage_db.py pattern)."""
    manager = DatabaseManager(db_url)
    original = transcript.db_manager
    transcript.set_db_manager(manager)
    yield manager
    transcript.set_db_manager(original)


def test_chat_tables_and_usage_fk_exist(db_url):
    engine = DatabaseManager(db_url).engine
    inspector = inspect(engine)
    names = set(inspector.get_table_names())
    assert {"conversations", "messages"} <= names
    fks = inspector.get_foreign_keys("usage")
    assert any(fk["referred_table"] == "conversations" for fk in fks)


def test_active_conversation_is_created_then_reused(store_db):
    first = transcript.get_or_create_active_conversation()
    second = transcript.get_or_create_active_conversation()
    assert first.conversation_id == second.conversation_id  # most-recent row reused
    assert first.history == []


def test_append_then_history_round_trips_in_order(store_db):
    ctx = transcript.get_or_create_active_conversation()
    transcript.append_message(ctx.conversation_id, "user", "hello")
    transcript.append_message(ctx.conversation_id, "assistant", "hi there")
    reloaded = transcript.get_or_create_active_conversation()
    assert reloaded.conversation_id == ctx.conversation_id
    assert reloaded.history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


def test_new_conversation_becomes_the_active_one(store_db):
    first = transcript.get_or_create_active_conversation()
    transcript.append_message(first.conversation_id, "user", "old thread")
    fresh = transcript.start_new_conversation()
    assert fresh.conversation_id != first.conversation_id
    assert fresh.history == []
    assert transcript.get_or_create_active_conversation().conversation_id == fresh.conversation_id


def test_active_conversation_is_user_scoped(store_db):
    mine = transcript.get_or_create_active_conversation()  # default user (conftest context)
    with as_user(OTHER_USER):
        with store_db.get_session() as s:
            s.merge(User(id=OTHER_USER, email="other@example.com"))
        theirs = transcript.get_or_create_active_conversation()
        assert theirs.conversation_id != mine.conversation_id  # FAILS if scoping is dropped
