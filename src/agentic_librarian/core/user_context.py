"""Per-execution-context user identity (Lift 1, ADR-048).

The SEC-001 trust boundary, extended: user identity is set ONLY by trusted code (the
FastAPI auth dependency, the CLI/dev entrypoints, the Dagster ingest) and read by the
MCP tools and the usage recorder. It is never a tool parameter — the LLM cannot see,
supply, or be prompt-injected into choosing a user."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from uuid import UUID

# The pre-multi-user operator account. Migration 0002 backfills every existing
# reading_history/suggestions row onto it; a constant (not a lookup) so migrations,
# entrypoints, and tests agree forever.
DEFAULT_USER_ID = UUID("00000000-0000-4000-8000-000000000001")

current_user_id: ContextVar[UUID | None] = ContextVar("current_user_id", default=None)


def get_required_user_id() -> UUID:
    """The current user's id, failing CLOSED: no identity context means no data access
    — never a fall-through to 'all rows'."""
    user_id = current_user_id.get()
    if user_id is None:
        raise RuntimeError(
            "No user identity in context. User-scoped operations require as_user(...) "
            "or the FastAPI auth dependency to have set the current user (ADR-048)."
        )
    return user_id


@contextmanager
def as_user(user_id: UUID | None):
    """Run a block as the given user — for entrypoints and tests."""
    token = current_user_id.set(user_id)
    try:
        yield
    finally:
        current_user_id.reset(token)
