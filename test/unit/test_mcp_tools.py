from __future__ import annotations

import json
from unittest.mock import MagicMock, patch
from uuid import uuid4

import pytest
from agentic_librarian.db.models import Author, Suggestions, Work, WorkContributor
from agentic_librarian.mcp.server import (
    check_reading_history,
    get_unacted_suggestions,
    get_user_trope_preferences,
    get_work_details,
    search_internal_database,
    update_reading_status,
)


@pytest.fixture
def standard_books():
    with open("test/data/standard_books.json") as f:
        return json.load(f)


@pytest.fixture
def mock_db_manager():
    with patch("agentic_librarian.mcp.server.db_manager") as mock:
        session = MagicMock()
        mock.get_session.return_value.__enter__.return_value = session
        yield mock


@pytest.fixture
def mock_trope_manager():
    with patch("agentic_librarian.mcp.server.TropeManager") as mock:
        tm_inst = mock.return_value
        tm_inst._get_embedding.return_value = [0.1] * 1536
        yield tm_inst


@pytest.fixture
def mock_style_manager():
    with patch("agentic_librarian.mcp.server.StyleManager") as mock:
        sm_inst = mock.return_value
        sm_inst._get_embedding.return_value = [0.2] * 1536
        yield sm_inst


def test_search_internal_database_mock(mock_db_manager, mock_trope_manager, mock_style_manager, standard_books):
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    # Setup mock return data using WorkContributor
    mock_works = []
    for b in standard_books:
        author = Author(name=b["author"])
        work = Work(id=uuid4(), title=b["title"])
        contributor = WorkContributor(work=work, author=author, role="Author")
        work.contributors = [contributor]
        mock_works.append(work)

    # To handle multiple different query chains, we use side_effect on session.query
    mock_query = MagicMock()
    session.query.return_value = mock_query

    # 1. Trope/Style search: return some mock objects with IDs
    mock_trope = MagicMock(id=uuid4())
    mock_style = MagicMock(id=uuid4())

    # We'll make most chain methods return the same mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.limit.return_value = mock_query
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.distinct.return_value = mock_query
    mock_query.options.return_value = mock_query  # Support .options()

    # Now set the 'all' results based on the context
    # This is tricky with simple mocks. Let's simplify:
    # Just ensure candidate_work_ids gets something.
    mock_query.all.side_effect = [
        [mock_trope],  # search_internal_database (tropes similarity)
        [(mock_works[0].id,)],  # search_internal_database (trope_works)
        [mock_style],  # search_internal_database (styles similarity)
        [(mock_works[1].id,)],  # search_internal_database (author_works)
        [],  # search_internal_database (work_styles)
        mock_works,  # search_internal_database (Final Works retrieval)
    ]

    results = search_internal_database(target_tropes=["fantasy"], target_styles=["grimdark"])
    assert len(results) > 0
    assert results[0]["title"] in [b["title"] for b in standard_books]


def test_get_unacted_suggestions_mock(mock_db_manager, mock_trope_manager, mock_style_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    mock_work = Work(title="Previously Suggested")
    mock_suggestion = Suggestions(work=mock_work, status="Suggested", justification="Matches vibe")

    mock_query = MagicMock()
    session.query.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.join.return_value = mock_query
    mock_query.options.return_value = mock_query  # Support .options()
    mock_query.all.return_value = [mock_suggestion]

    results = get_unacted_suggestions(target_tropes=["fantasy"], target_styles=["grimdark"])
    assert len(results) == 1
    assert results[0]["title"] == "Previously Suggested"


def test_check_reading_history_mock(mock_db_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    # Robust mock: use a more flexible chain to ensure 'first()' returns None
    mock_query = session.query.return_value
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.first.return_value = None

    res = check_reading_history("Unread Book", "Author")
    assert res["status"] == "Unread"
    assert res["is_re_read_candidate"] is True


def test_update_reading_status_mock(mock_db_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    mock_work = Work(id="work-uuid", title="Test Book")

    # Mock the work query
    mock_query = session.query.return_value
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = mock_work

    # Mock the edition query (filter_by)
    mock_query.filter_by.return_value.first.return_value = MagicMock()

    resp = update_reading_status("Test Book", "Author", "read")
    assert "Successfully updated" in resp
    session.add.assert_called()
    session.flush.assert_called()


def test_get_user_trope_preferences_mock(mock_db_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    session.query.return_value.join.return_value.join.return_value.join.return_value.join.return_value.filter.return_value.group_by.return_value.order_by.return_value.limit.return_value.all.return_value = [
        ("Fantasy", 5),
        ("Sci-Fi", 3),
    ]

    results = get_user_trope_preferences()
    assert results == ["Fantasy", "Sci-Fi"]


def test_get_work_details_mock(mock_db_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    work_id = str(uuid4())
    mock_work = Work(id=work_id, title="Test Work", description="A test book", genres=["Drama"])

    # Mock Trope relationship
    mock_trope = MagicMock()
    mock_trope.name = "Test Trope"
    mock_trope.description = "A trope for testing"
    mock_wt = MagicMock(trope=mock_trope, relevance_score=0.8, justification="manifests in test")
    mock_work.tropes = [mock_wt]

    # Mock Style relationship (Work Style)
    mock_style = MagicMock()
    mock_style.name = "Cinematic"
    mock_ws = MagicMock(style=mock_style, attribute_type="pacing")
    mock_work.styles = [mock_ws]

    # Mock Contributors (Author Style)
    mock_author = MagicMock()
    mock_style_author = MagicMock()
    mock_style_author.name = "Cynical"
    mock_ads = MagicMock(style=mock_style_author, attribute_type="tone")
    mock_author.styles = [mock_ads]
    mock_contributor = MagicMock(author=mock_author, role="Author")
    mock_work.contributors = [mock_contributor]

    session.query.return_value.filter_by.return_value.first.return_value = mock_work

    details = get_work_details(work_id)

    assert details["title"] == "Test Work"
    assert len(details["tropes"]) == 1
    assert details["tropes"][0]["name"] == "Test Trope"
    assert details["tropes"][0]["justification"] == "manifests in test"
    assert details["styles"]["pacing"] == "Cinematic"
    assert details["styles"]["tone"] == "Cynical"


def test_get_work_details_returns_empty_on_non_uuid():
    # A web-discovered candidate has no DB id; an agent may pass a title instead of a
    # UUID. That must return no details, not crash the run (the guard short-circuits
    # before any DB access, so no db_manager mock is needed).
    assert get_work_details("the daughters war") == {}
    assert get_work_details("") == {}


def test_parse_uuid_accepts_valid_and_rejects_garbage():
    from uuid import UUID

    from agentic_librarian.mcp import server

    valid = "0b54ee04-19b9-4cd9-a0a3-9bb9a89c0f1e"
    assert server._parse_uuid(valid) == UUID(valid)
    assert server._parse_uuid(f"  {valid}  ") == UUID(valid)  # whitespace tolerated
    assert server._parse_uuid("the daughters war") is None  # the REC-016 crash class
    assert server._parse_uuid(None) is None
    assert server._parse_uuid(42) is None
    assert server._parse_uuid("") is None  # falsy fast-path
    assert server._parse_uuid(UUID(valid)) == UUID(valid)  # already-parsed UUID passes through


def test_normalize_status_matches_case_insensitively():
    from agentic_librarian.mcp import server

    allowed = ("Accepted", "Dismissed", "Already Read")
    assert server._normalize_status("accepted", allowed) == "Accepted"
    assert server._normalize_status("ALREADY READ", allowed) == "Already Read"
    assert server._normalize_status("  Dismissed ", allowed) == "Dismissed"
    assert server._normalize_status("Banana", allowed) is None
    assert server._normalize_status(None, allowed) is None
    assert server._normalize_status(7, allowed) is None


def test_enrich_and_persist_work_rejects_invalid_input(capsys):
    from agentic_librarian.mcp import server

    assert server.enrich_and_persist_work(title="", author="A") is None
    assert server.enrich_and_persist_work(title="T", author="   ") is None
    assert server.enrich_and_persist_work(title="x" * 501, author="A") is None
    assert server.enrich_and_persist_work(title="T", author=None) is None  # type: ignore[arg-type]
    assert "rejected invalid" in capsys.readouterr().out  # visible, not silent (no-silent-except rule)
