import pytest

from agentic_librarian.db.models import Trope, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work

pytestmark = pytest.mark.db_integration


class _PassthroughTrope:
    """standardize_trope returns an exact-name Trope (no embedding) so names are assertable."""

    def __init__(self, session):
        self.session = session

    def standardize_trope(self, name, *a, **k):
        t = self.session.query(Trope).filter_by(name=name).first()
        if t is None:
            t = Trope(name=name)
            self.session.add(t)
            self.session.flush()
        return t

    def standardize_style(self, *a, **k):
        return None


def _row(**over):
    r = {"Title": "FB Flag Test", "Author_1": "A. Author", "format": "ebook", "genres": ["fantasy"], "moods": ["dark"]}
    r.update(over)
    return r


def _trope_names(session, work):
    return {session.get(Trope, wt.trope_id).name for wt in session.query(WorkTrope).filter_by(work_id=work.id).all()}


def test_fast_pass_writes_no_fallback_tropes(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        work = persist_enriched_work(session, _row(write_fallback_tropes=False), tm, tm)
        session.flush()
        assert session.query(WorkTrope).filter_by(work_id=work.id).count() == 0
        assert work.genres


def test_default_writes_fallback_when_no_real_tropes(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        work = persist_enriched_work(session, _row(Title="FB Default"), tm, tm)
        session.flush()
        assert "Fantasy" in _trope_names(session, work)


def test_fallback_skipped_when_work_already_has_real_trope(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        work = persist_enriched_work(
            session,
            _row(Title="FB HasReal", enriched_tropes=[{"trope_name": "Chosen One", "justification": "x"}]),
            tm,
            tm,
        )
        session.flush()
        persist_enriched_work(session, _row(Title="FB HasReal"), tm, tm)
        session.flush()
        assert _trope_names(session, work) == {"Chosen One"}
