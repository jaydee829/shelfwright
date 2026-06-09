import asyncio
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


class _FailingConversation:
    """Fires one activity event, then the backend fails mid-turn."""

    def __init__(self, on_event):
        self._on_event = on_event

    async def asend(self, message: str) -> str:
        self._on_event("agent", "Explorer")
        raise RuntimeError("backend down")

    def close(self):
        pass


def _collect(message, recorded, conversation=_FakeConversation):
    async def run():
        out = []
        async for line in stream.sse_turn(
            message=message,
            conversation=conversation,
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
    assert body.index("Explorer") < body.index("reply to: hi")
    assert "event: activity" in body
    assert "event: text" in body
    assert body.rstrip().endswith("event: done\ndata: {}")


def test_stream_persists_user_then_assistant():
    recorded = []
    _collect("hi", recorded)
    assert recorded == [("user", "hi"), ("assistant", "reply to: hi")]


def test_stream_emits_error_and_no_done_on_failure():
    recorded = []
    body = "".join(_collect("hi", recorded, conversation=_FailingConversation))
    assert "event: activity" in body  # the activity that fired before the failure still streamed
    assert "event: error" in body
    assert "backend down" in body
    assert "event: done" not in body  # NO false success signal
    assert "event: text" not in body
    assert recorded == []  # asend raised before any persistence
