"""record_llm_call is best-effort (Lift 1, ADR-048): a metering failure logs a warning
and NEVER raises — bookkeeping must not kill a conversation."""

import logging

from agentic_librarian.core import usage
from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user, current_user_id
from agentic_librarian.db.session import DatabaseManager


def test_record_llm_call_swallows_db_failure(caplog):
    original = usage.db_manager
    usage.set_db_manager(DatabaseManager("postgresql://x:x@nohost-never-resolves:1/x"))
    try:
        with caplog.at_level(logging.WARNING), as_user(DEFAULT_USER_ID):
            usage.record_llm_call(vendor="gemini", model="m", input_tokens=1, output_tokens=2)
    finally:
        usage.set_db_manager(original)
    assert "usage metering failed" in caplog.text


def test_record_llm_call_swallows_missing_context(caplog):
    # The suite-wide fixture sets an identity; shed it — the recorder must swallow
    # the resulting RuntimeError, not raise it into the conversation.
    token = current_user_id.set(None)
    try:
        with caplog.at_level(logging.WARNING):
            usage.record_llm_call(vendor="gemini", model="m", input_tokens=1, output_tokens=2)
    finally:
        current_user_id.reset(token)
    assert "usage metering failed" in caplog.text
