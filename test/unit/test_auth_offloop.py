"""#93: the auth dependency's verify+DB work must run off the event loop, while the
ContextVar set stays in the coroutine (the documented constraint)."""

import asyncio
import threading
import uuid

from agentic_librarian.api import auth as auth_mod
from agentic_librarian.core.user_context import current_user_id


def test_resolve_runs_in_worker_thread_and_contextvar_set_in_coroutine(monkeypatch):
    seen = {}

    def fake_resolve(token):
        seen["thread"] = threading.current_thread().name
        return auth_mod.AuthenticatedUser(id=uuid.uuid4(), email="x@y.z")

    monkeypatch.setattr(auth_mod, "_resolve_user", fake_resolve)

    async def _run():
        result = await auth_mod.get_current_user(authorization="Bearer sometoken")
        return result, current_user_id.get()

    result, ctx_value = asyncio.run(_run())
    assert seen["thread"] != threading.main_thread().name  # resolve ran off-loop
    assert ctx_value == result.id  # ContextVar visible in the coroutine's context


def test_firebase_init_race_is_serialized(monkeypatch):
    """Two threads racing _ensure_firebase_app must not surface ValueError (spurious 401)."""
    import threading as _threading

    calls = []

    def fake_get_app():
        raise ValueError("no app")

    def fake_initialize_app():
        calls.append(1)
        if len(calls) > 1:
            raise ValueError("The default Firebase app already exists")

    monkeypatch.setattr(auth_mod.firebase_admin, "get_app", fake_get_app)
    monkeypatch.setattr(auth_mod.firebase_admin, "initialize_app", fake_initialize_app)
    errors = []

    def _run():
        try:
            auth_mod._ensure_firebase_app()
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [_threading.Thread(target=_run) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert errors == []
