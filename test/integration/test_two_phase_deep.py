from datetime import date, timedelta

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user
from agentic_librarian.db.models import Edition, ReadingHistory, Work, WorkContributor, WorkTrope
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


class _FakeManager:
    def __init__(self, result):
        self._result = result

    def enrich(self, title, author, format="Paperback", **kwargs):
        return self._result


def _seed_work(manager, *, title, author, fmt="ebook"):
    from agentic_librarian.db.models import Author

    with manager.get_session() as s:
        work = Work(title=title)
        s.add(work)
        s.flush()
        a = Author(name=author)
        s.add(a)
        s.flush()
        s.add(WorkContributor(work_id=work.id, author_id=a.id, role="Author"))
        s.add(Edition(work_id=work.id, format=fmt))
        s.flush()
        return work.id


def test_enrich_deep_updates_same_work_idempotently(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase
    from agentic_librarian.scouts import style_manager, trope_manager

    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    monkeypatch.setattr(trope_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)
    monkeypatch.setattr(style_manager, "get_cached_embedding", lambda *a, **k: [0.1] * 1536)

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    deep = {"enriched_tropes": [{"trope_name": "Found Family", "relevance_score": 0.9}], "narrator_names": []}
    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda: _FakeManager(deep))

    work_id = _seed_work(manager, title="Dune", author="Frank Herbert")

    assert two_phase.enrich_deep(work_id) is True
    assert two_phase.enrich_deep(work_id) is True  # retry-safe (Cloud Tasks redelivery)

    with manager.get_session() as s:
        links = s.query(WorkTrope).filter_by(work_id=work_id).all()
        assert len(links) == 1  # single trope link despite two runs


def test_enrich_deep_returns_false_for_unknown_work(db_url, monkeypatch):
    from uuid import uuid4

    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    assert two_phase.enrich_deep(uuid4()) is False


def test_add_read_event_logs_and_dedups_rereads(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    work_id = _seed_work(manager, title="Hyperion", author="Dan Simmons")
    today = date.today()
    earlier = today - timedelta(days=30)

    with as_user(DEFAULT_USER_ID):
        first = two_phase.add_read_event(work_id, completed=today, rating=5, notes=None, fmt="ebook")
        dupe = two_phase.add_read_event(work_id, completed=today, rating=5, notes=None, fmt="ebook")
        reread = two_phase.add_read_event(work_id, completed=earlier, rating=4, notes=None, fmt="ebook")

    assert first == {"read_number": 1, "already_logged": False}
    assert dupe == {"read_number": 1, "already_logged": True}
    assert reread == {"read_number": 2, "already_logged": False}
    with manager.get_session() as s:
        rows = s.query(ReadingHistory).join(Edition).filter(Edition.work_id == work_id).all()
        assert len(rows) == 2
