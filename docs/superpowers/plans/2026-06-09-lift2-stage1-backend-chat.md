# Lift 2 Stage 1 — Backend Chat + Transcript Store Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the FastAPI service a Firebase-gated, SSE chat endpoint over the Librarian mesh whose conversation memory lives in Postgres, so chats survive Cloud Run recycling and become resumable.

**Architecture:** A new `chat` package owns the transcript (a `conversations`/`messages` schema) and the SSE turn loop. Each `/chat` turn loads the active conversation's prior messages, **rehydrates** them into a fresh ADK session (via `append_event`), runs the mesh while streaming agent-activity events, then persists the user message and the assistant reply. The conversation row's id IS the ADK session id, so the Lift 1 `usage.conversation_id` rows line up and gain a real FK.

**Tech Stack:** FastAPI (`StreamingResponse`, `text/event-stream`), SQLAlchemy + Alembic, `google-adk` sessions/events, pytest (`db_integration` + FastAPI `TestClient`).

**Scope note:** This is Stage 1 of 4 (spec `docs/superpowers/specs/2026-06-09-lift2-front-end-design.md`). The SPA (Stage 2), async enrichment (Stage 3), and IAM-gate/cleanups/rollout (Stage 4) are separate plans. **Beta streaming decision:** the mesh runs in ADK's default (non-streaming) mode — one final response per turn. So this stage streams **agent-activity live** and the **reply as a single final chunk**; token-by-token streaming (ADK `StreamingMode.SSE`) is deferred future work.

**Conventions (from Lift 1):** run all commands **inside the dev container**; in-container pre-commit (pinned ruff) is authoritative — bare `ruff check` gives false I001s. DB tests carry the `db_integration` marker and run against `agentic_librarian_test`. Every scoping test must FAIL when its filter is deleted (mutation mindset).

---

## File Structure

- `alembic/versions/<rev>_chat_transcript_store.py` — **Create.** Migration: `conversations`, `messages`, and the `usage.conversation_id` → `conversations.id` FK. Down-revision = current head `c804d02d6fbb`.
- `src/agentic_librarian/db/models.py` — **Modify.** Add `Conversation` and `Message` models; add the `ForeignKey("conversations.id")` to `Usage.conversation_id`.
- `src/agentic_librarian/chat/__init__.py` — **Create.** Empty package marker.
- `src/agentic_librarian/chat/transcript.py` — **Create.** User-scoped transcript store: resolve/create the active conversation, start a new one, load history, append a message.
- `src/agentic_librarian/agents/runtime.py` — **Modify.** Extend `astart_conversation` to accept an explicit `session_id` and a `history` list, seeding prior turns into the session via `append_event`.
- `src/agentic_librarian/chat/stream.py` — **Create.** The SSE turn loop: bridge the backend conversation's `on_event` + final reply into an async stream of SSE lines, and persist the transcript.
- `src/agentic_librarian/api/main.py` — **Modify.** Add `GET /conversations/current`, `POST /conversations`, and `POST /chat` (SSE), all behind `get_current_user`.
- `test/integration/test_transcript_store.py` — **Create.** Transcript store + scoping (db_integration).
- `test/unit/test_chat_rehydrate.py` — **Create.** Rehydration seeds session events.
- `test/unit/test_chat_stream.py` — **Create.** SSE turn loop with a fake backend.
- `test/integration/test_chat_api.py` — **Create.** The three endpoints end-to-end (db_integration).

---

## Task 1: Schema — conversations, messages, usage FK

**Files:**
- Create: `alembic/versions/<rev>_chat_transcript_store.py`
- Modify: `src/agentic_librarian/db/models.py`
- Test: `test/integration/test_transcript_store.py` (schema assertion only in this task)

- [ ] **Step 1: Generate an empty migration**

Run (in the dev container): `alembic revision -m "chat transcript store"`
This creates `alembic/versions/<rev>_chat_transcript_store.py` with `down_revision = "c804d02d6fbb"` auto-filled (the current head). Confirm that down_revision value; if it differs, the head moved — stop and reconcile.

- [ ] **Step 2: Write the migration body**

Replace the generated `upgrade()`/`downgrade()` with:

```python
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

def upgrade() -> None:
    op.create_table(
        "conversations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=False),
        sa.Column("title", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
        sa.Column("updated_at", sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
    )
    op.create_index("ix_conversations_user_id", "conversations", ["user_id"])
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), sa.ForeignKey("conversations.id"), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False, server_default=sa.text("timezone('utc', now())")),
    )
    op.create_index("ix_messages_conversation_id", "messages", ["conversation_id"])
    # The column exists from Lift 1 (migration c804d02d6fbb); add the FK now that its target exists.
    op.create_foreign_key("fk_usage_conversation_id", "usage", "conversations", ["conversation_id"], ["id"])

def downgrade() -> None:
    op.drop_constraint("fk_usage_conversation_id", "usage", type_="foreignkey")
    op.drop_index("ix_messages_conversation_id", table_name="messages")
    op.drop_table("messages")
    op.drop_index("ix_conversations_user_id", table_name="conversations")
    op.drop_table("conversations")
```

- [ ] **Step 3: Add the ORM models**

In `src/agentic_librarian/db/models.py`, add after the `Suggestions` class (the imports `UTC, datetime, UUID, uuid4, ForeignKey, String, Text, DateTime, PG_UUID, Mapped, mapped_column, relationship` are all already present):

```python
class Conversation(Base):
    """One chat thread (Lift 2). The active thread is the user's most-recent row;
    New chat inserts a new one. title is nullable now so the future switchable-list
    needs no migration. id doubles as the ADK session id so usage rows line up."""

    __tablename__ = "conversations"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(UTC), onupdate=lambda: datetime.now(UTC), nullable=False
    )

    messages: Mapped[list["Message"]] = relationship(back_populates="conversation", cascade="all, delete-orphan")


class Message(Base):
    """One turn in a conversation (Lift 2). role is 'user' or 'assistant'."""

    __tablename__ = "messages"

    id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid4, nullable=False)
    conversation_id: Mapped[UUID] = mapped_column(ForeignKey("conversations.id"), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), nullable=False)

    conversation: Mapped["Conversation"] = relationship(back_populates="messages")
```

Then change the `Usage.conversation_id` column to carry the FK:

```python
    conversation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id"), nullable=True
    )
```

- [ ] **Step 4: Write the failing schema test**

Create `test/integration/test_transcript_store.py`:

```python
import pytest
from sqlalchemy import inspect

from agentic_librarian.db.session import DatabaseManager


@pytest.mark.db_integration
def test_chat_tables_and_usage_fk_exist():
    engine = DatabaseManager().engine
    inspector = inspect(engine)
    names = set(inspector.get_table_names())
    assert {"conversations", "messages"} <= names
    fks = inspector.get_foreign_keys("usage")
    assert any(fk["referred_table"] == "conversations" for fk in fks)
```

If `DatabaseManager` exposes the engine under a different attribute, read `src/agentic_librarian/db/session.py` and use the correct accessor (e.g. `_engine`); adjust the test to match.

- [ ] **Step 5: Run it to verify it fails**

Run: `pytest test/integration/test_transcript_store.py -m db_integration -v`
Expected: FAIL — the test DB schema predates the new migration.

- [ ] **Step 6: Rebuild the test schema and verify it passes**

The schema is built once per session by conftest via `alembic upgrade head`. Drop and recreate so the new migration applies:
Run: `psql "$TEST_DATABASE_URL" -c 'DROP SCHEMA public CASCADE; CREATE SCHEMA public;'` (or `docker exec` into the db container, db `agentic_librarian_test`), then
Run: `pytest test/integration/test_transcript_store.py -m db_integration -v`
Expected: PASS.

- [ ] **Step 7: Verify migration fidelity (Lift 1 gate)**

Run: `alembic upgrade head && alembic downgrade -1 && alembic upgrade head`
Expected: no errors; the autogenerate check (env.py `compare_metadata`) shows the models and schema agree.

- [ ] **Step 8: Commit**

```bash
git add alembic/versions/ src/agentic_librarian/db/models.py test/integration/test_transcript_store.py
git commit -m "feat(lift2): chat transcript schema — conversations, messages, usage FK"
```

---

## Task 2: Transcript store (`chat/transcript.py`)

**Files:**
- Create: `src/agentic_librarian/chat/__init__.py` (empty)
- Create: `src/agentic_librarian/chat/transcript.py`
- Test: `test/integration/test_transcript_store.py` (append to it)

- [ ] **Step 1: Write the failing tests**

Append to `test/integration/test_transcript_store.py`:

```python
from uuid import UUID

from agentic_librarian.chat import transcript
from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user

OTHER_USER = UUID("00000000-0000-4000-8000-0000000000ff")


@pytest.mark.db_integration
def test_active_conversation_is_created_then_reused():
    first = transcript.get_or_create_active_conversation()
    second = transcript.get_or_create_active_conversation()
    assert first.conversation_id == second.conversation_id  # most-recent row reused
    assert first.history == []


@pytest.mark.db_integration
def test_append_then_history_round_trips_in_order():
    ctx = transcript.get_or_create_active_conversation()
    transcript.append_message(ctx.conversation_id, "user", "hello")
    transcript.append_message(ctx.conversation_id, "assistant", "hi there")
    reloaded = transcript.get_or_create_active_conversation()
    assert reloaded.conversation_id == ctx.conversation_id
    assert reloaded.history == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]


@pytest.mark.db_integration
def test_new_conversation_becomes_the_active_one():
    first = transcript.get_or_create_active_conversation()
    transcript.append_message(first.conversation_id, "user", "old thread")
    fresh = transcript.start_new_conversation()
    assert fresh.conversation_id != first.conversation_id
    assert fresh.history == []
    assert transcript.get_or_create_active_conversation().conversation_id == fresh.conversation_id


@pytest.mark.db_integration
def test_active_conversation_is_user_scoped():
    mine = transcript.get_or_create_active_conversation()  # default user (conftest context)
    with as_user(OTHER_USER):
        # Seed the other user so the FK holds, then check isolation.
        from agentic_librarian.db.models import User
        from agentic_librarian.db.session import DatabaseManager

        with DatabaseManager().get_session() as s:
            s.merge(User(id=OTHER_USER, email="other@example.com"))
        theirs = transcript.get_or_create_active_conversation()
        assert theirs.conversation_id != mine.conversation_id  # FAILS if the query forgets user scoping
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest test/integration/test_transcript_store.py -m db_integration -v`
Expected: FAIL — `agentic_librarian.chat.transcript` does not exist.

- [ ] **Step 3: Implement the store**

Create `src/agentic_librarian/chat/__init__.py` (empty). Create `src/agentic_librarian/chat/transcript.py`:

```python
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
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest test/integration/test_transcript_store.py -m db_integration -v`
Expected: PASS (all cases).

- [ ] **Step 5: Mutation check (scoping is real)**

Temporarily delete `.filter(Conversation.user_id == user_id)` from `get_or_create_active_conversation`. Run the suite; `test_active_conversation_is_user_scoped` MUST fail. Restore the filter.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/chat/ test/integration/test_transcript_store.py
git commit -m "feat(lift2): user-scoped chat transcript store"
```

---

## Task 3: Rehydration — seed an ADK session from stored history

**Files:**
- Modify: `src/agentic_librarian/agents/runtime.py:118-128` (`astart_conversation`/`start_conversation`)
- Test: `test/unit/test_chat_rehydrate.py`

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_chat_rehydrate.py`:

```python
import asyncio

from agentic_librarian.agents import runtime


class _FakeSessionService:
    def __init__(self):
        self.created = []
        self.appended = []

    async def create_session(self, app_name, user_id, session_id):
        self.created.append(session_id)
        return object()

    async def get_session(self, app_name, user_id, session_id):
        return object()

    async def append_event(self, session, event):
        self.appended.append(event)
        return event


class _FakeRunner:
    def __init__(self):
        self.session_service = _FakeSessionService()


def test_history_is_seeded_as_events_in_order():
    runner = _FakeRunner()
    history = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi there"},
    ]
    conv = asyncio.run(
        runtime.astart_conversation(user_id="u", runner=runner, session_id="abc123", history=history)
    )
    assert conv.session_id == "abc123"
    assert runner.session_service.created == ["abc123"]
    # Two prior turns -> two seeded events, oldest first, with the right content roles.
    assert len(runner.session_service.appended) == 2
    roles = [e.content.role for e in runner.session_service.appended]
    assert roles == ["user", "model"]
    texts = [e.content.parts[0].text for e in runner.session_service.appended]
    assert texts == ["hello", "hi there"]


def test_no_history_seeds_no_events():
    runner = _FakeRunner()
    asyncio.run(runtime.astart_conversation(user_id="u", runner=runner, session_id="abc", history=None))
    assert runner.session_service.appended == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest test/unit/test_chat_rehydrate.py -v`
Expected: FAIL — `astart_conversation` takes no `session_id`/`history`.

- [ ] **Step 3: Implement rehydration**

In `src/agentic_librarian/agents/runtime.py`, add the import near the top (with the other `google.adk`/`google.genai` imports):

```python
from google.adk.events import Event
```

Replace `astart_conversation` and `start_conversation` (currently lines ~118-128) with:

```python
async def astart_conversation(
    user_id: str = "local",
    runner: Runner | None = None,
    on_event=None,
    session_id: str | None = None,
    history: list[dict] | None = None,
) -> LibrarianConversation:
    """Open a conversation. `session_id` lets the caller pin the ADK session id to a
    stored conversation id (so usage rows line up). `history` (oldest first, each
    {'role': 'user'|'assistant', 'content': str}) is seeded into the session as events
    so the mesh has prior context WITHOUT re-running earlier turns (Lift 2)."""
    runner = runner or build_runner()
    session_id = session_id or uuid.uuid4().hex
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=user_id, session_id=session_id
    )
    for turn in history or []:
        role = "user" if turn["role"] == "user" else "model"
        author = "user" if turn["role"] == "user" else "librarian"
        content = types.Content(role=role, parts=[types.Part(text=turn["content"])])
        await runner.session_service.append_event(session, Event(author=author, content=content))
    return LibrarianConversation(runner, user_id, session_id, on_event=on_event)


def start_conversation(
    user_id: str = "local", runner: Runner | None = None, on_event=None,
    session_id: str | None = None, history: list[dict] | None = None,
) -> LibrarianConversation:
    return asyncio.run(
        astart_conversation(
            user_id=user_id, runner=runner, on_event=on_event, session_id=session_id, history=history
        )
    )
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest test/unit/test_chat_rehydrate.py -v`
Expected: PASS.

- [ ] **Step 5: Verify the existing conversation tests still pass**

Run: `pytest test/unit -k "conversation or runtime" -v`
Expected: PASS — the new params are optional and default to today's behavior.

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/agents/runtime.py test/unit/test_chat_rehydrate.py
git commit -m "feat(lift2): rehydrate ADK sessions from stored history via append_event"
```

---

## Task 4: SSE turn loop (`chat/stream.py`)

**Files:**
- Create: `src/agentic_librarian/chat/stream.py`
- Test: `test/unit/test_chat_stream.py`

The turn loop bridges the backend's synchronous `on_event(kind, detail)` callback and its single final reply into an ordered async stream of SSE lines, persisting the transcript at the end. It is backend-agnostic: tests drive it with a fake conversation, no ADK.

- [ ] **Step 1: Write the failing test**

Create `test/unit/test_chat_stream.py`:

```python
import asyncio
import json
from uuid import UUID

from agentic_librarian.chat import stream

_USER = UUID("00000000-0000-4000-8000-000000000001")


class _FakeConversation:
    """Mimics a BackendConversation: fires on_event during asend, returns a final reply."""

    def __init__(self, on_event):
        self._on_event = on_event

    async def asend(self, message: str) -> str:
        self._on_event("agent", "Explorer")
        self._on_event("tool", "search_internal_database")
        return f"reply to: {message}"

    def close(self):
        pass


def _collect(message, recorded):
    async def run():
        out = []
        async for line in stream.sse_turn(
            message=message,
            conversation=_FakeConversation,  # factory: takes on_event
            on_persist=lambda role, content: recorded.append((role, content)),
            user_id=_USER,
        ):
            out.append(line)
        return out

    return asyncio.run(run())


def test_stream_emits_activity_then_text_then_done():
    recorded = []
    lines = _collect("hi", recorded)
    body = "".join(lines)
    # Activity events arrive before the reply; stream terminates with done.
    assert body.index("Explorer") < body.index("reply to: hi")
    assert "event: activity" in body
    assert "event: text" in body
    assert body.rstrip().endswith("event: done\ndata: {}")


def test_stream_persists_user_then_assistant():
    recorded = []
    _collect("hi", recorded)
    assert recorded == [("user", "hi"), ("assistant", "reply to: hi")]
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest test/unit/test_chat_stream.py -v`
Expected: FAIL — `agentic_librarian.chat.stream` does not exist.

- [ ] **Step 3: Implement the turn loop**

Create `src/agentic_librarian/chat/stream.py`:

```python
"""SSE turn loop (Lift 2). Bridges a backend conversation's on_event callback and its
single final reply into an ordered text/event-stream, persisting the transcript.

Beta scope: agent-activity streams live; the reply is one final chunk (the mesh runs
in ADK's default non-streaming mode). Token-level streaming is future work."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Callable
from uuid import UUID

from agentic_librarian.core.user_context import as_user

_DONE = object()  # sentinel marking the queue's end


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def sse_turn(
    message: str,
    conversation: Callable,
    on_persist: Callable[[str, str], None],
    user_id: UUID,
) -> AsyncIterator[str]:
    """Run one turn. `conversation` is a factory taking an on_event callback and
    returning an object with `async asend(message) -> str` and `close()`. `on_persist`
    stores one (role, content) message. `user_id` re-establishes identity inside the
    turn: the SSE generator runs on the event loop after the endpoint returns, where the
    auth dependency's ContextVar is no longer active — so the mesh tools, usage metering,
    and the transcript writes would otherwise see no user (the Lift 1 _with_user lesson)."""
    queue: asyncio.Queue = asyncio.Queue()

    def on_event(kind: str, detail: str) -> None:
        queue.put_nowait(_sse("activity", {"kind": kind, "detail": detail}))

    conv = conversation(on_event)

    async def drive() -> None:
        try:
            with as_user(user_id):  # identity live for the mesh tools, usage, and persist
                reply = await conv.asend(message)
                on_persist("user", message)
                on_persist("assistant", reply)
            queue.put_nowait(_sse("text", {"text": reply}))
        finally:
            conv.close()
            queue.put_nowait(_DONE)

    task = asyncio.create_task(drive())
    try:
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            yield item
        yield _sse("done", {})
    finally:
        await task  # surface any exception from the driver
```

- [ ] **Step 4: Run to verify it passes**

Run: `pytest test/unit/test_chat_stream.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/agentic_librarian/chat/stream.py test/unit/test_chat_stream.py
git commit -m "feat(lift2): SSE chat turn loop with activity streaming + transcript persist"
```

---

## Task 5: API endpoints — /conversations + /chat

**Files:**
- Modify: `src/agentic_librarian/api/main.py`
- Test: `test/integration/test_chat_api.py`

Wire the store (Task 2), rehydration (Task 3), and turn loop (Task 4) together. `/chat` opens the ADK conversation with `session_id = conversation_id.hex` and the stored history, then streams.

- [ ] **Step 1: Write the failing test**

Create `test/integration/test_chat_api.py`:

```python
import pytest
from fastapi.testclient import TestClient

from agentic_librarian.api import auth, main
from agentic_librarian.core.user_context import DEFAULT_USER_ID, DEFAULT_USER_EMAIL


@pytest.fixture
def client(monkeypatch):
    # Bypass Firebase: the auth dependency resolves the default user directly.
    async def _fake_dep():
        from agentic_librarian.core.user_context import current_user_id
        user = auth.AuthenticatedUser(id=DEFAULT_USER_ID, email=DEFAULT_USER_EMAIL)
        current_user_id.set(user.id)
        return user

    main.app.dependency_overrides[auth.get_current_user] = _fake_dep
    # Make /chat deterministic: a fake backend that needs no LLM. _open_conversation is
    # async, so the replacement must be an async function (it is awaited in _SyncOpener).
    class _FakeConv:
        async def asend(self, message): return f"echo:{message}"
        def close(self): ...
    async def _fake_open(**kwargs): return _FakeConv()
    monkeypatch.setattr(main, "_open_conversation", _fake_open)
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


@pytest.mark.db_integration
def test_current_conversation_then_chat_then_resume(client):
    current = client.get("/conversations/current").json()
    assert current["messages"] == []
    cid = current["id"]

    with client.stream("POST", "/chat", json={"message": "hi"}) as r:
        body = "".join(chunk for chunk in r.iter_text())
    assert "echo:hi" in body
    assert body.rstrip().endswith("event: done\ndata: {}")

    resumed = client.get("/conversations/current").json()
    assert resumed["id"] == cid
    assert resumed["messages"] == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "echo:hi"},
    ]


@pytest.mark.db_integration
def test_new_conversation_starts_empty(client):
    client.get("/conversations/current")
    fresh = client.post("/conversations").json()
    assert fresh["messages"] == []
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest test/integration/test_chat_api.py -m db_integration -v`
Expected: FAIL — the routes and `_open_conversation` don't exist.

- [ ] **Step 3: Implement the endpoints**

In `src/agentic_librarian/api/main.py`, add imports:

```python
from agentic_librarian.agents.runtime import astart_conversation
from agentic_librarian.chat import stream, transcript
from fastapi import Body
from fastapi.responses import StreamingResponse
```

Add a seam for the backend conversation (so tests can replace it) and the routes:

```python
async def _open_conversation(*, user_id, session_id, history, on_event):
    """Open the mesh conversation for one turn (seam: tests replace this)."""
    return await astart_conversation(
        user_id=user_id, session_id=session_id, history=history, on_event=on_event
    )


@app.get("/conversations/current")
def get_current_conversation(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    ctx = transcript.get_or_create_active_conversation()
    return {"id": str(ctx.conversation_id), "messages": ctx.history}


@app.post("/conversations")
def new_conversation(user: AuthenticatedUser = Depends(get_current_user)):  # noqa: B008
    ctx = transcript.start_new_conversation()
    return {"id": str(ctx.conversation_id), "messages": ctx.history}


@app.post("/chat")
def chat(user: AuthenticatedUser = Depends(get_current_user), message: str = Body(..., embed=True)):  # noqa: B008
    ctx = transcript.get_or_create_active_conversation()
    adk_user_id = str(user.id)  # ADK wants a string user id; as_user wants the UUID
    return StreamingResponse(
        stream.sse_turn(
            message=message,
            conversation=lambda on_event: _SyncOpener(adk_user_id, ctx, on_event),
            on_persist=lambda role, content: transcript.append_message(ctx.conversation_id, role, content),
            user_id=user.id,
        ),
        media_type="text/event-stream",
    )
```

`_SyncOpener` bridges the async `astart_conversation` into `sse_turn`'s synchronous factory by opening the
conversation lazily on the first `asend`, inside the request's running event loop. Add it near the routes:

```python
class _SyncOpener:
    """Lazily opens the async mesh conversation on first asend, inside the running loop.
    session_id = the conversation id so usage rows (keyed off the ADK session uuid) FK to it."""

    def __init__(self, user_id, ctx, on_event):
        self._user_id, self._ctx, self._on_event, self._conv = user_id, ctx, on_event, None

    async def asend(self, message: str) -> str:
        if self._conv is None:
            self._conv = await _open_conversation(
                user_id=self._user_id, session_id=self._ctx.conversation_id.hex,
                history=self._ctx.history, on_event=self._on_event,
            )
        return await self._conv.asend(message)

    def close(self):
        if self._conv is not None:
            self._conv.close()
```

The test replaces `main._open_conversation`, so `_SyncOpener` calls the fake conversation instead of the mesh.

- [ ] **Step 4: Run to verify it passes**

Run: `pytest test/integration/test_chat_api.py -m db_integration -v`
Expected: PASS.

- [ ] **Step 5: Full suite + lint**

Run: `pytest -m "not live and not api_dependent" -q` then `pre-commit run --all-files`
Expected: green (in-container pre-commit is authoritative).

- [ ] **Step 6: Commit**

```bash
git add src/agentic_librarian/api/main.py test/integration/test_chat_api.py
git commit -m "feat(lift2): /chat SSE endpoint + /conversations resume & new"
```

---

## Task 6: Usage rows line up with conversations (FK proof)

**Files:**
- Test: `test/integration/test_chat_api.py` (append)

This task adds no production code — it proves the design choice (conversation id == ADK session id) makes Lift 1 usage rows FK-valid, so a future regression that breaks the alignment is caught.

- [ ] **Step 1: Write the guard test**

Append to `test/integration/test_chat_api.py`:

```python
@pytest.mark.db_integration
def test_usage_rows_reference_the_conversation(client, monkeypatch):
    # Drive a turn whose fake backend records a usage row against the session id,
    # mirroring runtime._record_event_usage, and assert the FK resolves.
    from uuid import UUID
    from agentic_librarian.core import usage

    current = client.get("/conversations/current").json()
    cid = UUID(current["id"])

    class _UsingConv:
        async def asend(self, message):
            usage.record_llm_call(vendor="gemini", model="test", input_tokens=1,
                                  output_tokens=1, conversation_id=cid)
            return "ok"
        def close(self): ...
    async def _using_open(**kwargs): return _UsingConv()
    monkeypatch.setattr(main, "_open_conversation", _using_open)

    with client.stream("POST", "/chat", json={"message": "go"}) as r:
        "".join(r.iter_text())

    from agentic_librarian.db.models import Usage
    from agentic_librarian.db.session import DatabaseManager
    with DatabaseManager().get_session() as s:
        row = s.query(Usage).filter(Usage.conversation_id == cid).first()
        assert row is not None  # FK held: the conversation existed when usage was written
```

- [ ] **Step 2: Run it**

Run: `pytest test/integration/test_chat_api.py::test_usage_rows_reference_the_conversation -m db_integration -v`
Expected: PASS (the conversation row is created by `/conversations/current` before the usage insert; the FK from Task 1 resolves).

- [ ] **Step 3: Mutation check**

Temporarily drop `fk_usage_conversation_id` (or point the insert at a random UUID): writing usage with a non-existent `conversation_id` must raise an IntegrityError. Confirm, then restore.

- [ ] **Step 4: Commit**

```bash
git add test/integration/test_chat_api.py
git commit -m "test(lift2): usage rows FK-resolve to their conversation"
```

---

## Final review

After all tasks: dispatch a final code reviewer over the whole Stage 1 diff, then use superpowers:finishing-a-development-branch. Stage 1 ships as its own PR (Gemini review → CI green → squash-merge "(#N)"), and the live `/chat` smoke is deferred to Stage 4's rollout (it needs the prod Gemini key + open IAM gate). Stage 2 (the SPA) is planned against this merged contract.

## Self-review notes (author)

- The `_SyncOpener` indirection exists because `astart_conversation` is async but `sse_turn`'s `conversation` factory is called synchronously then `asend`-ed inside the loop — opening lazily on first `asend` keeps everything on the request's event loop. If the implementer finds a cleaner bridge (e.g. making `sse_turn` accept an async factory), that is welcome — keep the tests green.
- True token streaming is intentionally out of scope (see header). If the mesh is later switched to `StreamingMode.SSE`, `_record_event_usage`'s "one usage event per call" assumption (runtime.py) must be revisited.
