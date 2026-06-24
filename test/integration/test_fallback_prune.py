import pytest

from agentic_librarian.db.models import Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import trope_backfill as tb

pytestmark = pytest.mark.db_integration


def _link(session, work, name):
    t = Trope(name=name)
    session.add(t)
    session.flush()
    session.add(WorkTrope(work_id=work.id, trope_id=t.id))


def test_prune_removes_genre_mood_fallbacks_keeps_real_and_stopgap(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        # cleaned genres/moods are what the prune matches a trope's cleaned name against
        polluted = Work(title="Polluted", genres=["Science Fiction", "Fantasy", "Literary"], moods=["Tense"])
        fallback_only = Work(title="Fallback Only", genres=["Fantasy"], moods=[])
        session.add_all([polluted, fallback_only])
        session.flush()
        # genuine narrative tropes -> clean to something NOT in genres/moods -> KEEP
        _link(session, polluted, "The Dark Night of the Soul")
        _link(session, polluted, "Mirror / Shadow Self")
        # genre/mood fallbacks -> clean INTO the work's genres/moods -> PRUNE
        _link(session, polluted, "science-fiction-fantasy")  # -> [Science Fiction, Fantasy] <= gm
        _link(session, polluted, "literary-fiction")  # -> [Literary] <= gm
        _link(session, polluted, "tense")  # -> [Tense] <= moods
        # a work whose ONLY trope is a genre fallback (no real) -> keep it (stopgap)
        _link(session, fallback_only, "fantasy")  # -> [Fantasy] <= gm, but no real trope
        session.flush()

        deleted = tb.apply_fallback_prune(session)
        session.flush()

        assert deleted == 3
        pol = {
            session.get(Trope, wt.trope_id).name for wt in session.query(WorkTrope).filter_by(work_id=polluted.id).all()
        }
        assert pol == {"The Dark Night of the Soul", "Mirror / Shadow Self"}  # real tropes survive
        assert session.query(WorkTrope).filter_by(work_id=fallback_only.id).count() == 1  # stopgap kept

        assert tb.apply_fallback_prune(session) == 0  # idempotent
