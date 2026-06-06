"""The scoping seam (Lift 1, ADR-048): identity rides a ContextVar set only by
trusted code. Fail CLOSED: no context, no data access."""

from uuid import UUID, uuid4

import pytest
from agentic_librarian.core.user_context import (
    DEFAULT_USER_ID,
    as_user,
    current_user_id,
    get_required_user_id,
)


@pytest.fixture(autouse=True)
def _clear_context():
    """These tests manage context explicitly — neutralize any suite-wide default."""
    token = current_user_id.set(None)
    yield
    current_user_id.reset(token)


def test_default_user_id_is_the_pinned_constant():
    assert DEFAULT_USER_ID == UUID("00000000-0000-4000-8000-000000000001")


def test_get_required_user_id_fails_closed_when_unset():
    with pytest.raises(RuntimeError, match="No user identity in context"):
        get_required_user_id()


def test_as_user_sets_and_restores():
    uid = uuid4()
    with as_user(uid):
        assert get_required_user_id() == uid
    with pytest.raises(RuntimeError):
        get_required_user_id()


def test_as_user_nesting_restores_outer():
    outer, inner = uuid4(), uuid4()
    with as_user(outer):
        with as_user(inner):
            assert get_required_user_id() == inner
        assert get_required_user_id() == outer


def test_as_user_restores_on_exception():
    with pytest.raises(ValueError), as_user(uuid4()):
        raise ValueError("boom")
    with pytest.raises(RuntimeError):
        get_required_user_id()
