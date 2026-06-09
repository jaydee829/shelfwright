"""SSE turn loop (Lift 2). Bridges a backend conversation's on_event callback and its
single final reply into an ordered text/event-stream, persisting the transcript.

Beta scope: agent-activity streams live; on success the reply is one `text` event then
`done`; on failure a single `error` event ends the stream (never a false `done`).
Token-level streaming is future work (the mesh runs in ADK's default non-streaming mode)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import AsyncGenerator, Callable
from uuid import UUID

from agentic_librarian.core.user_context import as_user

logger = logging.getLogger(__name__)

_DONE = object()  # sentinel marking the end of the live activity stream


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


async def sse_turn(
    message: str,
    conversation: Callable,
    on_persist: Callable[[str, str], None],
    user_id: UUID,
) -> AsyncGenerator[str, None]:
    """Run one turn as an SSE stream. `conversation` is a factory taking an on_event
    callback and returning an object with `async asend(message) -> str` and `close()`.
    `on_persist` stores one (role, content) message.

    Activity events stream live as the mesh works; then on success a `text` event + `done`,
    or on failure a single `error` event (never a false `done`). `user_id` re-establishes
    identity inside the turn: the generator runs on the event loop after the endpoint
    returns, where the auth dependency's ContextVar is no longer active — so the mesh tools,
    usage metering, and transcript writes would otherwise see no user (the Lift 1 _with_user
    lesson)."""
    queue: asyncio.Queue = asyncio.Queue()  # carries live activity events only

    def on_event(kind: str, detail: str) -> None:
        queue.put_nowait(_sse("activity", {"kind": kind, "detail": detail}))

    conv = conversation(on_event)

    async def drive() -> str:
        """Run the turn; return the reply (or raise). Persists inside as_user so the
        mesh tools and transcript writes see the right user."""
        try:
            with as_user(user_id):
                reply = await conv.asend(message)
                on_persist("user", message)
                on_persist("assistant", reply)
            return reply
        finally:
            conv.close()
            queue.put_nowait(_DONE)

    task = asyncio.create_task(drive())
    try:
        # Stream live activity until the driver signals it has finished.
        while True:
            item = await queue.get()
            if item is _DONE:
                break
            yield item
        # The turn is finished: surface the reply, or a single error event — never a false done.
        try:
            reply = await task
        except Exception:  # noqa: BLE001 - one bad turn ends the stream cleanly, not crash it
            logger.warning("chat turn failed", exc_info=True)
            yield _sse("error", {"detail": "The Librarian hit a problem. Please try again."})
            return
        yield _sse("text", {"text": reply})
        yield _sse("done", {})
    finally:
        # If the consumer went away (client disconnect -> GeneratorExit) while the turn
        # was still in flight, cancel the mesh instead of leaking a running task that
        # keeps burning tokens. No-op on the normal/error paths (task already done).
        if not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
