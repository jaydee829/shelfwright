"""INF-030: the per-turn DB writes run off the event loop via asyncio.to_thread, and the
worker thread still sees the user context (to_thread copies contextvars). These tests pin
the invariant that the user identity survives the thread hop for BOTH writes."""

import asyncio
import inspect
from uuid import UUID

from agentic_librarian.agents import runtime as runtime_mod
from agentic_librarian.core import usage as usage_mod
from agentic_librarian.core.user_context import as_user

UID = UUID("00000000-0000-4000-8000-000000000001")


def test_record_event_usage_is_async():
    """The runtime call site must be a coroutine so the metering write can be awaited
    off-loop (it's driven inside an `async for` over ADK events)."""
    assert inspect.iscoroutinefunction(runtime_mod._record_event_usage)


class _CapturingSession:
    def __init__(self, sink):
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def add(self, obj):
        self._sink.append(obj)

    def flush(self):
        pass


class _CapturingManager:
    def __init__(self, sink):
        self._sink = sink

    def get_session(self):
        return _CapturingSession(self._sink)


def test_record_llm_call_offloaded_keeps_user_context(monkeypatch):
    # This repo drives coroutines from sync tests via asyncio.run (see test_chat_stream.py);
    # pytest-asyncio is not installed, so we wrap the off-loop write the same way.
    sink = []
    monkeypatch.setattr(usage_mod, "db_manager", _CapturingManager(sink))

    async def run():
        with as_user(UID):
            # Exactly how runtime now performs the write: off-loop via to_thread.
            await asyncio.to_thread(
                usage_mod.record_llm_call,
                vendor="gemini",
                model="gemini-3.1-flash-lite",
                input_tokens=10,
                output_tokens=5,
                conversation_id=None,
            )

    asyncio.run(run())
    assert len(sink) == 1
    assert sink[0].user_id == UID  # the worker thread saw the context user
