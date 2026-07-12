"""#93/#94 final-review fix: check_availability must never let a batch_availability
exception escape into the agent loop — pool contention makes such failures likelier
now that tool bodies run off-loop."""

from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest

from agentic_librarian.mcp import server as mcp_server


@pytest.fixture
def mock_db_manager():
    with patch.object(mcp_server, "db_manager") as mock:
        session = MagicMock()
        mock.get_session.return_value.__enter__.return_value = session
        yield mock


def test_check_availability_survives_batch_lookup_exception(monkeypatch, mock_db_manager):
    monkeypatch.setattr(mcp_server, "get_required_user_id", lambda: uuid4())

    session = mock_db_manager.get_session.return_value.__enter__.return_value
    mock_lib = MagicMock(library_slug="my-library", display_name="My Library")
    mock_query = session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.all.return_value = [mock_lib]

    with patch.object(
        mcp_server.availability_service, "batch_availability", side_effect=RuntimeError("thunder is down")
    ):
        result = mcp_server.check_availability("Title", "Author")

    assert result["libraries"] == []
    assert result["note"] == "Couldn't confirm live availability — offer the search links."
