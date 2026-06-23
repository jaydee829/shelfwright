import pytest

from agentic_librarian.db.models import Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import trope_backfill as tb

pytestmark = pytest.mark.db_integration


def _link(session, work, name, justification):
    t = Trope(name=name)
    session.add(t)
    session.flush()
    session.add(WorkTrope(work_id=work.id, trope_id=t.id, justification=justification))


def test_prune_removes_fallbacks_only_on_works_with_real_tropes(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        polluted = Work(title="Polluted")
        clean_only = Work(title="Fallback Only")
        session.add_all([polluted, clean_only])
        session.flush()
        _link(session, polluted, "Chosen One", "scout says so")  # real
        _link(session, polluted, "science-fiction-fantasy", None)  # fallback
        _link(session, polluted, "tense", None)  # fallback
        _link(session, clean_only, "literary-fiction", None)  # fallback, but NO real -> keep
        session.flush()

        deleted = tb.apply_fallback_prune(session)
        session.flush()

        assert deleted == 2
        pol = {
            session.get(Trope, wt.trope_id).name for wt in session.query(WorkTrope).filter_by(work_id=polluted.id).all()
        }
        assert pol == {"Chosen One"}
        assert session.query(WorkTrope).filter_by(work_id=clean_only.id).count() == 1  # untouched

        assert tb.apply_fallback_prune(session) == 0  # idempotent
