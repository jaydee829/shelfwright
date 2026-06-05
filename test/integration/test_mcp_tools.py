import json
from unittest.mock import patch
from uuid import UUID

import pytest
from agentic_librarian.db.models import Author, ReadingHistory, Suggestions, Trope, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp import server as mcp_server
from agentic_librarian.mcp.server import (
    check_reading_history,
    db_manager,
    get_unacted_suggestions,
    log_suggestion,
    search_internal_database,
    set_db_manager,
    update_reading_status,
    update_suggestion_status,
)
from sqlalchemy import text


@pytest.fixture
def standard_books():
    with open("test/data/standard_books.json") as f:
        return json.load(f)


@pytest.mark.db_integration
def test_mcp_discovery_and_filtering_real_db(db_url, standard_books):
    """Verify high-level MCP tool interactions using a real database."""
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)

    with test_db_manager.get_session() as session:
        session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        # 1. Seed DB
        for book in standard_books:
            author = Author(name=book["author"])
            session.add(author)
            session.flush()

            work = Work(title=book["title"], genres=book["genres"])
            session.add(work)
            session.flush()

            wc = WorkContributor(work=work, author=author, role="Author")
            session.add(wc)
            session.flush()

            for trope_name in book["tropes"]:
                trope = Trope(name=trope_name, embedding=[0.1] * 1536)
                session.add(trope)
                session.flush()
                wt = WorkTrope(work=work, trope=trope)
                session.add(wt)

        # Commit the seed: the MCP tools open their own independent sessions
        # (coarse-grained, ADR-013), so they can only see committed data.
        session.commit()

        # 2. Verify search_internal_database
        with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", return_value=[0.1] * 1536):
            results = search_internal_database(target_tropes=["any"])
            assert len(results) > 0

            # 3. Verify check_reading_history (Initial: Unread)
            status = check_reading_history(standard_books[0]["title"], standard_books[0]["author"])
            assert status["status"] == "Unread"

            # 4. Verify update_reading_status
            update_reading_status(standard_books[0]["title"], standard_books[0]["author"], "read")

            # 5. Verify check_reading_history (Now: Read)
            status_after = check_reading_history(standard_books[0]["title"], standard_books[0]["author"])
            assert status_after["status"] == "Read"


@pytest.mark.db_integration
def test_suggestion_persistence_real_db(db_url, standard_books):
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)
    with test_db_manager.get_session() as session:
        # Create a work to suggest
        author = Author(name="Persistence Author")
        session.add(author)
        session.flush()
        work = Work(title="Persistent Title")
        session.add(work)
        session.flush()
        wc = WorkContributor(work=work, author=author, role="Author")
        session.add(wc)
        session.flush()

        # Commit the seed: log_suggestion opens its own session and references
        # work_id as an FK, so the work must be committed first.
        session.commit()

        # Log it
        log_suggestion(work_id=str(work.id), context="Vibe", justification="Logic")

        # Verify it shows up in unacted suggestions. Patch the embedding so semantic ranking
        # needs no API call — db_integration now runs in CI with only a dummy key.
        with patch("agentic_librarian.mcp.server.TropeManager._get_embedding", return_value=[0.1] * 1536):
            results = get_unacted_suggestions(target_tropes=["any"])
        assert any(r["title"] == "Persistent Title" for r in results)


@pytest.fixture
def seeded_work_id(db_url):
    """Create a minimal Work (with one contributor) in the test DB and return its str UUID.
    Follows the seeding style of test_suggestion_persistence_real_db: create a local
    DatabaseManager, call set_db_manager, seed inside a committed session."""
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)
    with test_db_manager.get_session() as session:
        author = Author(name="Security Test Author")
        session.add(author)
        session.flush()
        work = Work(title="Security Test Work")
        session.add(work)
        session.flush()
        wc = WorkContributor(work=work, author=author, role="Author")
        session.add(wc)
        session.flush()
        session.commit()
        return str(work.id)


@pytest.mark.db_integration
def test_log_suggestion_rejects_invalid_and_missing_work(db_url):
    # SEC-002: ids are validated upfront; a valid-but-unknown UUID is rejected by a
    # referent check, not by an IntegrityError.
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)
    assert "Error" in log_suggestion(
        work_id="the daughters war", context="rec", justification="x"
    )
    missing = "0b54ee04-19b9-4cd9-a0a3-9bb9a89c0f1e"
    out = log_suggestion(work_id=missing, context="rec", justification="x")
    assert "Error" in out and "no work exists" in out
    with mcp_server.db_manager.get_session() as session:
        assert session.query(Suggestions).count() == 0  # rejections wrote NOTHING


@pytest.mark.db_integration
def test_log_suggestion_caps_freetext_lengths(db_url, seeded_work_id):
    # justification/context are truncated (free text by design), not rejected.
    out = log_suggestion(
        work_id=seeded_work_id, context="c" * 500, justification="j" * 5000,
        conversation_id="not-a-uuid",
    )
    assert "Logged suggestion" in out
    with mcp_server.db_manager.get_session() as session:
        row = session.query(Suggestions).filter_by(work_id=seeded_work_id).order_by(
            Suggestions.suggested_at.desc()
        ).first()
        assert len(row.justification) == 2000
        assert len(row.context) == 200
        assert row.conversation_id is None


@pytest.mark.db_integration
def test_update_suggestion_status_enforces_enum(db_url, seeded_work_id):
    log_suggestion(work_id=seeded_work_id, context="rec", justification="x")
    out = update_suggestion_status(work_id=seeded_work_id, status="Banana")
    assert "Error" in out and "Accepted" in out  # error names the allowed values
    with mcp_server.db_manager.get_session() as session:
        row = session.query(Suggestions).filter_by(work_id=seeded_work_id).order_by(
            Suggestions.suggested_at.desc()
        ).first()
        assert row.status == "Suggested"  # rejection did not mutate
    # Case-insensitive normalization to the canonical value:
    out = update_suggestion_status(work_id=seeded_work_id, status="already read")
    assert "Already Read" in out
    with mcp_server.db_manager.get_session() as session:
        row = session.query(Suggestions).filter_by(work_id=seeded_work_id).order_by(
            Suggestions.suggested_at.desc()
        ).first()
        assert row.status == "Already Read"


@pytest.mark.db_integration
def test_update_reading_status_rejects_unknown_status_instead_of_false_success(db_url, seeded_work_id):
    # SEC-002 regression: unknown statuses previously returned "Successfully updated..."
    # while writing NOTHING. They must now return an honest error and write nothing.
    with mcp_server.db_manager.get_session() as session:
        work = session.get(Work, UUID(seeded_work_id))
        title = work.title
        author = work.contributors[0].author.name
        before = session.query(ReadingHistory).count()
    out = mcp_server.update_reading_status(title=title, author=author, status="abandoned")
    assert "Error" in out and "read" in out  # names the allowed values
    with mcp_server.db_manager.get_session() as session:
        assert session.query(ReadingHistory).count() == before  # nothing written


@pytest.mark.db_integration
def test_update_reading_status_validates_title_author_shape(db_url):
    test_db_manager = DatabaseManager(db_url)
    set_db_manager(test_db_manager)
    assert "Error" in mcp_server.update_reading_status(title="  ", author="A", status="read")
    assert "Error" in mcp_server.update_reading_status(title="T", author="", status="read")
    assert "Error" in mcp_server.update_reading_status(title="x" * 501, author="A", status="read")
