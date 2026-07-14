from unittest.mock import patch

import pytest
from sqlalchemy import text

from agentic_librarian.db.models import Trope, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work
from agentic_librarian.scouts.style_manager import StyleManager
from agentic_librarian.scouts.trope_manager import TropeManager

pytestmark = pytest.mark.db_integration

UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


class _PassthroughTrope:
    """standardize_trope/get_or_create_fallback_trope both return an exact-name Trope (no
    embedding) so we can assert names; each call is recorded so tests can assert which method
    the fallback vs. enriched branches actually dispatch to (#70)."""

    def __init__(self, session):
        self.session = session
        self.standardize_trope_calls: list[str] = []
        self.fallback_calls: list[str] = []

    def _get_or_create(self, name):
        t = self.session.query(Trope).filter_by(name=name).first()
        if t is None:
            t = Trope(name=name)
            self.session.add(t)
            self.session.flush()
        return t

    def standardize_trope(self, name, *a, **k):
        self.standardize_trope_calls.append(name)
        return self._get_or_create(name)

    def get_or_create_fallback_trope(self, name, *a, **k):
        self.fallback_calls.append(name)
        return self._get_or_create(name)

    def standardize_style(self, *a, **k):
        return None


def test_fallback_tropes_are_cleaned(db_url):
    manager = DatabaseManager(db_url)
    row = {
        "Title": "Fallback Trope Test",
        "Author_1": "T. Author",
        "format": "ebook",
        "genres": [f"science-fiction-fantasy-{UUID}"],
        "moods": [],
        # no enriched_tropes -> fallback path fires; skip_enrichment must be falsy
    }
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        work = persist_enriched_work(session, row, tm, tm)
        session.flush()
        names = {
            session.get(Trope, wt.trope_id).name for wt in session.query(WorkTrope).filter_by(work_id=work.id).all()
        }
        assert names == {"Science Fiction", "Fantasy"}  # cleaned + split, NOT the raw slug


def test_fallback_branch_dispatches_to_get_or_create_fallback_trope_not_standardize(db_url):
    """#70: the fallback (genre/mood) branch must call get_or_create_fallback_trope — never
    standardize_trope, whose 0.85 semantic match is the pollution mechanism."""
    manager = DatabaseManager(db_url)
    row = {
        "Title": "Fallback Dispatch Test",
        "Author_1": "T. Author",
        "format": "ebook",
        "genres": [f"science-fiction-fantasy-{UUID}"],
        "moods": [],
    }
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        persist_enriched_work(session, row, tm, tm)
        session.flush()
        assert set(tm.fallback_calls) == {"Science Fiction", "Fantasy"}
        assert tm.standardize_trope_calls == []


def test_enriched_branch_still_dispatches_to_standardize_trope(db_url):
    """The real (scout-supplied) trope branch is unaffected — it must keep using
    standardize_trope's semantic matching, not the new exact-name-only method."""
    manager = DatabaseManager(db_url)
    row = {
        "Title": "Enriched Dispatch Test",
        "Author_1": "T. Author",
        "format": "ebook",
        "enriched_tropes": [{"trope_name": "Chosen One", "justification": "x"}],
        "genres": [],
        "moods": [],
    }
    with manager.get_session() as session:
        tm = _PassthroughTrope(session)
        persist_enriched_work(session, row, tm, tm)
        session.flush()
        assert tm.standardize_trope_calls == ["Chosen One"]
        assert tm.fallback_calls == []


def test_fallback_tag_never_lands_on_a_real_semantically_close_trope(db_url, monkeypatch):
    """#70 end-to-end regression: seed a real trope "The Dark Night of the Soul" with an
    embedding, then persist a work whose only mood is "Dark" (no enriched tropes,
    write_fallback_tropes=True) using an embedding stub that makes "Dark" cosine-IDENTICAL to
    that real trope. Before the fix, persist's fallback branch called standardize_trope, whose
    0.85 semantic match would land "Dark" on "The Dark Night of the Soul". After the fix, the
    work's link must go to a NEW trope named exactly "Dark"."""
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    manager = DatabaseManager(db_url)
    real_trope_embedding = [0.42] * 1536

    with manager.get_session() as session:
        session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
        real_trope = Trope(name="The Dark Night of the Soul", embedding=real_trope_embedding)
        session.add(real_trope)
        session.flush()
        real_trope_id = real_trope.id  # capture before the session closes (DetachedInstanceError)
        session.commit()

    with manager.get_session() as session:
        tm = TropeManager(session=session)
        sm = StyleManager(session=session)
        row = {
            "Title": "Dark Fallback Collision Test",
            "Author_1": "T. Author",
            "format": "ebook",
            "genres": [],
            "moods": ["Dark"],
            "write_fallback_tropes": True,
        }
        # Every embed call (including the real trope's own re-embed, which never happens here
        # since it's pre-seeded) returns the SAME vector as the real trope's, so a semantic
        # (cosine) match would be a guaranteed hit if the fallback path used standardize_trope.
        with patch.object(TropeManager, "_get_embedding", return_value=real_trope_embedding):
            work = persist_enriched_work(session, row, tm, sm)
            session.flush()

        linked_names = {
            session.get(Trope, wt.trope_id).name for wt in session.query(WorkTrope).filter_by(work_id=work.id).all()
        }
        assert linked_names == {"Dark"}
        assert "The Dark Night of the Soul" not in linked_names

        dark_trope = session.query(Trope).filter_by(name="Dark").one()
        assert dark_trope.id != real_trope_id
