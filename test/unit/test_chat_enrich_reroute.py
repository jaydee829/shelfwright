"""Chat contract (user decision 2026-07-12): chat adds run the fast pass + queue the deep
pass; the user-facing message says the Librarian is still investigating."""

import uuid
from unittest.mock import patch

from agentic_librarian.mcp import server as mcp_server


def test_enrich_tool_routes_through_two_phase_and_enqueues():
    wid = uuid.uuid4()
    with (
        patch.object(mcp_server.two_phase, "enrich_fast", return_value=(wid, True)) as fast,
        patch.object(mcp_server, "enqueue_enrichment", return_value=True) as enq,
    ):
        result = mcp_server.enrich_and_persist_work(title="Dune", author="Frank Herbert")
    assert result == str(wid)
    fast.assert_called_once()
    enq.assert_called_once_with(str(wid))


def test_enrich_tool_dedup_hit_does_not_reenqueue():
    wid = uuid.uuid4()
    with (
        patch.object(mcp_server.two_phase, "enrich_fast", return_value=(wid, False)),
        patch.object(mcp_server, "enqueue_enrichment") as enq,
    ):
        result = mcp_server.enrich_and_persist_work(title="Dune", author="Frank Herbert")
    assert result == str(wid)
    enq.assert_not_called()


def test_enrich_tool_not_found_returns_none():
    with patch.object(mcp_server.two_phase, "enrich_fast", return_value=None):
        assert mcp_server.enrich_and_persist_work(title="Ghost", author="Nobody") is None


def test_add_book_message_mentions_background_analysis(monkeypatch):
    wid = uuid.uuid4()
    monkeypatch.setattr(mcp_server, "get_required_user_id", lambda: uuid.uuid4())
    with (
        patch.object(mcp_server.two_phase, "enrich_fast", return_value=(wid, True)),
        patch.object(mcp_server, "enqueue_enrichment", return_value=True),
        patch.object(mcp_server.two_phase, "add_read_event", return_value={"read_number": 1, "already_logged": False}),
    ):
        msg = mcp_server.add_book_to_history(title="Dune", author="Frank Herbert")
    assert "background" in msg.lower()  # the Librarian relays this to the user
    assert "Dune" in msg


def test_add_book_existing_work_has_no_background_note(monkeypatch):
    wid = uuid.uuid4()
    monkeypatch.setattr(mcp_server, "get_required_user_id", lambda: uuid.uuid4())
    with (
        patch.object(mcp_server.two_phase, "enrich_fast", return_value=(wid, False)),
        patch.object(mcp_server, "enqueue_enrichment") as enq,
        patch.object(mcp_server.two_phase, "add_read_event", return_value={"read_number": 2, "already_logged": False}),
    ):
        msg = mcp_server.add_book_to_history(title="Dune", author="Frank Herbert")
    assert "background" not in msg.lower()
    enq.assert_not_called()
