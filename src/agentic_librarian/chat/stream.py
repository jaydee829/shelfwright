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
