import pytest

from agentic_librarian.db.models import Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import trope_backfill as tb

pytestmark = pytest.mark.db_integration

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


def test_apply_splits_dirty_trope_and_migrates_links(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        dirty = Trope(name=f"science-fiction-fantasy-{UUID}")
        w = Work(title="Trope Split Test")
        session.add_all([dirty, w])
        session.flush()
        session.add(WorkTrope(work_id=w.id, trope_id=dirty.id, relevance_score=0.9))
        session.flush()

        tb.apply_trope_changes(session, trope_manager=None, changes=None)  # None tm -> null embedding
        session.flush()

        names = {t.name for t in session.query(Trope).all()}
        assert "Science Fiction" in names and "Fantasy" in names
        assert f"science-fiction-fantasy-{UUID}" not in names  # dirty row gone
        linked = {session.get(Trope, wt.trope_id).name for wt in session.query(WorkTrope).filter_by(work_id=w.id).all()}
        assert linked == {"Science Fiction", "Fantasy"}  # link split, preserved


def test_apply_is_idempotent(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        t = Trope(name=f"audiobook-{UUID}")  # pure junk -> dropped
        w = Work(title="Junk Trope Test")
        session.add_all([t, w])
        session.flush()
        session.add(WorkTrope(work_id=w.id, trope_id=t.id))
        session.flush()

        tb.apply_trope_changes(session, trope_manager=None)
        session.flush()
        assert session.query(WorkTrope).filter_by(work_id=w.id).count() == 0
        # second run is a no-op
        assert tb.apply_trope_changes(session, trope_manager=None) == 0
