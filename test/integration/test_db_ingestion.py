import uuid
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.scouts.trope_manager import TropeManager
from sqlalchemy import text


@pytest.mark.db_integration
def test_full_ingestion_flow_real_db(db_url):
    """
    Verify the full relational chain can be created in a single transaction.
    This tests the 'flush' logic implicitly by ensuring IDs are available for FKs.
    """
    db_manager = DatabaseManager(db_url)
    with db_manager.get_session() as session:
        # 1. Create Author
        author = Author(name=f"Integration Author {uuid.uuid4()}")
        session.add(author)
        session.flush()
        assert author.id is not None

        # 2. Create Work linked to Author via WorkContributor
        work = Work(title=f"Integration Work {uuid.uuid4()}")
        session.add(work)
        session.flush()
        assert work.id is not None

        wc = WorkContributor(work=work, author=author, role="Author")
        session.add(wc)
        session.flush()

        # 3. Create Edition linked to Work
        edition = Edition(work=work, format="hardcover", page_count=100)
        session.add(edition)
        session.flush()
        assert edition.id is not None

        # 4. Create Reading History linked to Edition
        history = ReadingHistory(edition=edition, date_completed=date(2024, 1, 1))
        session.add(history)
        session.flush()
        assert history.id is not None

        # Verify we can query it back and travers relationships
        session.expire_all()  # Force reload from DB
        saved_history = session.query(ReadingHistory).filter_by(id=history.id).one()
        assert saved_history.edition.work.contributors[0].author.name == author.name


@pytest.mark.db_integration
def test_trope_manager_real_db(db_url):
    """Verify TropeManager works with a real database including vector extensions."""
    db_manager = DatabaseManager(db_url)
    with db_manager.get_session() as session:
        # Ensure pgvector extension is available in the test environment
        session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        # Mock the Gemini client to avoid external API dependency
        with patch("agentic_librarian.scouts.trope_manager.genai.Client") as mock_genai:
            mock_client = mock_genai.return_value
            mock_response = MagicMock()
            mock_response.embeddings = [MagicMock(values=[0.1] * 1536)]
            mock_client.models.embed_content.return_value = mock_response

            tm = TropeManager(session=session, api_key="fake_key")

            # 1. Create a new trope
            trope_name = f"Trope {uuid.uuid4()}"
            trope = tm.standardize_trope(trope_name)

            # This assertion verifies the 'session.flush()' inside tm.standardize_trope
            assert trope.id is not None

            # 2. Link to a work
            work = Work(title=f"Work {uuid.uuid4()}")
            session.add(work)
            session.flush()

            wt = WorkTrope(work=work, trope=trope)
            session.add(wt)
            session.flush()

            # Verify the link exists
            session.expire_all()
            saved_link = session.query(WorkTrope).filter_by(work_id=work.id, trope_id=trope.id).first()
            assert saved_link is not None
            assert saved_link.work.title == work.title
