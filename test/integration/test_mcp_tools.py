import json
from unittest.mock import patch

import pytest
from agentic_librarian.db.models import Author, Trope, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.mcp.server import (
    check_reading_history,
    get_unacted_suggestions,
    log_suggestion,
    search_internal_database,
    set_db_manager,
    update_reading_status,
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

        # Verify it shows up in unacted suggestions
        results = get_unacted_suggestions(target_tropes=["any"])
        assert any(r["title"] == "Persistent Title" for r in results)
