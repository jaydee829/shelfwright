"""The happy path writes one row per call, stamped with the context user."""

import pytest

from agentic_librarian.core import usage
from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import Usage
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


def test_record_llm_call_writes_row(db_url):
    manager = DatabaseManager(db_url)
    original = usage.db_manager
    usage.set_db_manager(manager)
    try:
        usage.record_llm_call(vendor="gemini", model="gemini-test", input_tokens=10, output_tokens=4)
    finally:
        usage.set_db_manager(original)
    with manager.get_session() as session:
        row = session.query(Usage).one()
        assert row.user_id == DEFAULT_USER_ID
        assert row.key_source == "app"
        assert (row.vendor, row.model, row.input_tokens, row.output_tokens) == ("gemini", "gemini-test", 10, 4)
