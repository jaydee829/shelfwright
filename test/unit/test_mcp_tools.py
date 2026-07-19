from __future__ import annotations

import json
from datetime import date, timedelta
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
    update_suggestion_status,
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

    # session.query(...).all() calls, in order: active-suggestion exclusion set, trope
    # prefilter, style prefilter, final Work retrieval (#125 rewrite — the ranked pool
    # itself now comes from session.execute(<select>).all(), mocked separately below).
    mock_query.all.side_effect = [
        [],  # active-suggestion exclusion set (none suggested)
        [mock_trope],  # nearest-tag trope prefilter
        [mock_style],  # nearest-tag style prefilter
        mock_works,  # final Work retrieval
    ]

    # session.execute(<select>).all() calls, in order: trope-rank, work-style-rank,
    # author-style-rank — each returns (work_id, score) rows.
    mock_execute_results = [
        [(mock_works[0].id, 0.1)],  # trope-rank
        [(mock_works[1].id, 0.2)],  # work-style-rank
        [],  # author-style-rank
    ]
    session.execute.side_effect = [MagicMock(all=MagicMock(return_value=rows)) for rows in mock_execute_results]

    results = search_internal_database(target_tropes=["fantasy"], target_styles=["grimdark"])
    assert len(results) == 2
    assert results[0]["id"] == str(mock_works[0].id)  # lower (better) score ranks first
    assert results[1]["id"] == str(mock_works[1].id)
    for row, work in zip(results, [mock_works[0], mock_works[1]], strict=True):
        assert row["title"] == work.title
        assert row["authors"] == [c.author.name for c in work.contributors]
        assert row["genres"] == work.genres
        assert row["description"] == work.description


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
    _mock_work_lookup(session)

    with patch("agentic_librarian.mcp.server.two_phase.add_read_event") as mock_add_read_event:
        mock_add_read_event.return_value = {"read_number": 1, "already_logged": False}
        resp = update_reading_status("Test Book", "Author", "read")

    assert "Successfully updated" in resp
    mock_add_read_event.assert_called_once()


def _mock_work_lookup(session, work_id="work-uuid", editions=()):
    """update_reading_status's rewritten body needs the work lookup to resolve an id, plus
    an edition-format lookup (same session, same mocked query chain) — the read-event write
    itself goes through two_phase.add_read_event (mocked separately per test), not this
    session. `editions` stubs the edition-format query's .all() result."""
    mock_work = Work(id=work_id, title="Test Book")
    mock_query = session.query.return_value
    mock_query.join.return_value = mock_query
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = mock_work
    mock_query.all.return_value = list(editions)
    return mock_work


def test_update_reading_status_honors_year(mock_db_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value
    _mock_work_lookup(session)

    with patch("agentic_librarian.mcp.server.two_phase.add_read_event") as mock_add_read_event:
        mock_add_read_event.return_value = {"read_number": 1, "already_logged": False}
        resp = update_reading_status("Test Book", "Author", "read", year=2019)

    assert mock_add_read_event.call_args.kwargs["completed"] == date(2019, 1, 1)
    assert "assumed" not in resp
    assert "Successfully updated" in resp


def test_update_reading_status_rejects_bad_year(mock_db_manager):
    resp = update_reading_status("Test Book", "Author", "read", year=1200)
    assert "Error" in resp
    assert f"1900 and {date.today().year}" in resp


def test_update_reading_status_flags_assumed_today(mock_db_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value
    _mock_work_lookup(session)

    with patch("agentic_librarian.mcp.server.two_phase.add_read_event") as mock_add_read_event:
        mock_add_read_event.return_value = {"read_number": 1, "already_logged": False}
        resp = update_reading_status("Test Book", "Author", "read")

    assert mock_add_read_event.call_args.kwargs["completed"] == date.today()
    assert "assumed" in resp


def test_update_reading_status_rejects_future_date(mock_db_manager):
    tomorrow = (date.today() + timedelta(days=1)).isoformat()
    resp = update_reading_status("Test Book", "Author", "read", date_completed=tomorrow)
    assert "Error" in resp
    assert "future" in resp


def test_update_reading_status_reuses_sole_edition_format(mock_db_manager):
    """Exactly one edition on the work -> its format is reused instead of "Unknown"."""
    session = mock_db_manager.get_session.return_value.__enter__.return_value
    sole_edition = MagicMock(format="audiobook")
    _mock_work_lookup(session, editions=[sole_edition])

    with patch("agentic_librarian.mcp.server.two_phase.add_read_event") as mock_add_read_event:
        mock_add_read_event.return_value = {"read_number": 1, "already_logged": False}
        resp = update_reading_status("Test Book", "Author", "read")

    assert mock_add_read_event.call_args.kwargs["fmt"] == "audiobook"
    assert "Successfully updated" in resp


@pytest.mark.parametrize(
    "editions",
    [
        [],
        [MagicMock(format="ebook"), MagicMock(format="audiobook")],
    ],
    ids=["zero_editions", "two_editions"],
)
def test_update_reading_status_falls_back_to_unknown_format(mock_db_manager, editions):
    """Zero or multiple (ambiguous) editions -> "Unknown", same as before the fix."""
    session = mock_db_manager.get_session.return_value.__enter__.return_value
    _mock_work_lookup(session, editions=editions)

    with patch("agentic_librarian.mcp.server.two_phase.add_read_event") as mock_add_read_event:
        mock_add_read_event.return_value = {"read_number": 1, "already_logged": False}
        update_reading_status("Test Book", "Author", "read")

    assert mock_add_read_event.call_args.kwargs["fmt"] == "Unknown"


def test_update_suggestion_status_reports_already_resolved_when_active_absent(mock_db_manager):
    """GH #130 fix: history writes auto-resolve the active pick, so the charter's mandated
    follow-up 'update_suggestion_status' call finds no ACTIVE row on the most common feedback
    path. Instead of the old, agent-confusing "No active suggestion found", it should calmly
    report the most recent resolved status and write nothing."""
    session = mock_db_manager.get_session.return_value.__enter__.return_value
    mock_query = session.query.return_value
    mock_query.filter_by.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    resolved_row = MagicMock(status="Read")
    mock_query.first.side_effect = [None, resolved_row]  # 1st: active lookup, 2nd: most-recent-any

    resp = update_suggestion_status(work_id=str(uuid4()), status="Dismissed")

    assert "already resolved" in resp
    assert "Read" in resp
    session.flush.assert_not_called()  # no status was written


def test_update_suggestion_status_reports_no_active_suggestion_when_none_exist(mock_db_manager):
    session = mock_db_manager.get_session.return_value.__enter__.return_value
    mock_query = session.query.return_value
    mock_query.filter_by.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.first.side_effect = [None, None]  # no active row, no row at all

    work_id = str(uuid4())
    resp = update_suggestion_status(work_id=work_id, status="Dismissed")

    assert resp == f"No active suggestion found for work {work_id}."
    session.flush.assert_not_called()


def test_get_user_trope_preferences_mock(mock_db_manager):
    """Feed counts where raw frequency and lift DISAGREE (Fantasy has more raw links but
    is merely ubiquitous in the catalog; Sci-Fi is rarer but the user over-indexes on it)
    and assert the lift order — not the raw-frequency order — comes back."""
    session = mock_db_manager.get_session.return_value.__enter__.return_value

    user_counts_query = MagicMock()
    user_counts_query.join.return_value = user_counts_query
    user_counts_query.filter.return_value = user_counts_query
    user_counts_query.group_by.return_value = user_counts_query
    user_counts_query.all.return_value = [("Fantasy", 5), ("Sci-Fi", 3)]

    user_works_query = MagicMock()
    user_works_query.join.return_value = user_works_query
    user_works_query.filter.return_value = user_works_query
    user_works_query.scalar.return_value = 10

    catalog_counts_query = MagicMock()
    catalog_counts_query.join.return_value = catalog_counts_query
    catalog_counts_query.filter.return_value = catalog_counts_query
    catalog_counts_query.group_by.return_value = catalog_counts_query
    catalog_counts_query.all.return_value = [("Fantasy", 90), ("Sci-Fi", 4)]

    catalog_works_query = MagicMock()
    catalog_works_query.join.return_value = catalog_works_query
    catalog_works_query.filter.return_value = catalog_works_query
    catalog_works_query.scalar.return_value = 100

    session.query.side_effect = [
        user_counts_query,
        user_works_query,
        catalog_counts_query,
        catalog_works_query,
    ]

    results = get_user_trope_preferences()
    # Raw frequency would put Fantasy (5) above Sci-Fi (3); lift flips it: Fantasy is
    # near-ubiquitous in the catalog (90/100) while Sci-Fi is scarce but user-favored (4/100).
    assert results == ["Sci-Fi", "Fantasy"]


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


def test_enrich_and_persist_work_rejects_invalid_input(caplog):
    from agentic_librarian.mcp import server

    with caplog.at_level("WARNING", logger="agentic_librarian.mcp.server"):
        assert server.enrich_and_persist_work(title="", author="A") is None
        assert server.enrich_and_persist_work(title="T", author="   ") is None
        assert server.enrich_and_persist_work(title="x" * 501, author="A") is None
        assert server.enrich_and_persist_work(title="T", author=None) is None  # type: ignore[arg-type]
    assert "rejected invalid" in caplog.text  # visible, not silent (no-silent-except rule)


@pytest.mark.parametrize("raw", ["Removed", "removed", "REMOVED"])
def test_suggestion_statuses_include_neutral_removed(raw):
    # GH #130: the chat door to neutral removal — 'Removed' is canonical vocabulary
    # and case-normalizes like the other statuses.
    from agentic_librarian.mcp.server import _SUGGESTION_STATUSES, _normalize_status

    assert "Removed" in _SUGGESTION_STATUSES
    assert _normalize_status(raw, _SUGGESTION_STATUSES) == "Removed"
