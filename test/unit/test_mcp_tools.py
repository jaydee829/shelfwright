from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from agentic_librarian.db.models import Author, Suggestions, Work, WorkContributor
from agentic_librarian.mcp.server import (
    check_reading_history,
    get_unacted_suggestions,
    get_user_trope_preferences,
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


def test_search_internal_database_mock(mock_db_manager, mock_trope_manager, standard_books):
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    # Setup mock return data using WorkContributor
    mock_works = []
    for b in standard_books:
        author = Author(name=b["author"])
        work = Work(title=b["title"])
        contributor = WorkContributor(work=work, author=author, role="Author")
        work.contributors = [contributor]
        mock_works.append(work)

    # Mock the trope query chain and then the final work query
    session.query.return_value.order_by.return_value.limit.return_value.all.return_value = []  # Tropes
    session.query.return_value.join.return_value.filter.return_value.distinct.return_value.limit.return_value.all.return_value = mock_works

    results = search_internal_database(target_tropes=["fantasy"])
    assert len(results) == len(standard_books)
    assert results[0]["title"] == standard_books[0]["title"]


def test_get_unacted_suggestions_mock(mock_db_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    mock_work = Work(title="Previously Suggested")
    mock_suggestion = Suggestions(work=mock_work, status="Suggested", justification="Matches vibe")
    session.query.return_value.filter.return_value.join.return_value.limit.return_value.all.return_value = [
        mock_suggestion
    ]

    results = get_unacted_suggestions(target_tropes=["any"])
    assert len(results) == 1
    assert results[0]["title"] == "Previously Suggested"


def test_check_reading_history_mock(mock_db_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    # Robust mock: use a more flexible chain to ensure 'first()' returns None
    mock_query = session.query.return_value
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = None

    res = check_reading_history("Unread Book", "Author")
    assert res["status"] == "Unread"


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

    session.query.return_value.join.return_value.join.return_value.join.return_value.join.return_value.group_by.return_value.order_by.return_value.limit.return_value.all.return_value = [
        ("Fantasy", 5),
        ("Sci-Fi", 3),
    ]

    results = get_user_trope_preferences()
    assert results == ["Fantasy", "Sci-Fi"]
