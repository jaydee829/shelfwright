from datetime import date, timedelta

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID, as_user
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Work, WorkContributor, WorkTrope
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
    deep = {
        "enriched_tropes": [{"trope_name": "Found Family", "relevance_score": 0.9}],
        "narrator_names": [],
        "contributors": [
            {"name": "Frank Herbert", "role": "Author"},
            {"name": "Kevin J. Anderson", "role": "Author"},  # deep pass discovers a co-author
        ],
    }
    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda: _FakeManager(deep))

    work_id = _seed_work(manager, title="Dune", author="Frank Herbert")

    assert two_phase.enrich_deep(work_id) == "done"
    assert two_phase.enrich_deep(work_id) == "done"  # retry-safe (Cloud Tasks redelivery)

    with manager.get_session() as s:
        links = s.query(WorkTrope).filter_by(work_id=work_id).all()
        assert len(links) == 1  # single trope link despite two runs
        # GH #97: the write session stamps deep_enriched_at on a successful ("done") persist.
        work = s.query(Work).filter_by(title="Dune").one()
        assert work.deep_enriched_at is not None

        # GH #96: a co-author discovered by the deep pass must be LINKED on the existing work
        # (previously the WorkContributor dangled and SQLAlchemy 2.0 silently dropped it), and
        # no orphan Author rows may accumulate.
        work = s.query(Work).filter_by(title="Dune").one()
        roles = {(c.author.name, c.role) for c in work.contributors}
        assert ("Kevin J. Anderson", "Author") in roles  # the co-author the fake deep scout returns
        orphans = (
            s.query(Author)
            .outerjoin(WorkContributor, WorkContributor.author_id == Author.id)
            .filter(WorkContributor.author_id.is_(None))
            .count()
        )
        assert orphans == 0


def test_enrich_deep_returns_missing_not_done_when_nothing_was_persisted(db_url, monkeypatch):
    """Final-review Minor 7 (honesty): if the scouts return a row but persist_enriched_work
    ends up with NOTHING to attach it to (raw_contributors empties out after the malformed-name
    filter — e.g. the deep scout's only contributor entry has no usable name), nothing is
    persisted and deep_enriched_at is never stamped. enrich_deep must report "missing", not lie
    and say "done" — the caller (api/internal.py) maps "missing" to a non-retryable 404, while
    "done" would tell Cloud Tasks (and the operator) a pass succeeded when it did nothing at
    all."""
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    # A deep-scout result with contributors that all fail the malformed-name filter (persist.py's
    # raw_contributors comprehension drops entries with no usable string name) — this is the
    # concrete way persist_enriched_work returns None without touching the DB at all, standing in
    # for "the work's identity vanished mid-pass and nothing could be attached."
    deep = {
        "enriched_tropes": [],
        "narrator_names": [],
        "contributors": [{"name": None, "role": "Author"}],
    }
    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda: _FakeManager(deep))

    work_id = _seed_work(manager, title="Vanishing Work", author="Some Author")

    assert two_phase.enrich_deep(work_id) == "missing"

    with manager.get_session() as s:
        work = s.get(Work, work_id)
        # nothing was persisted or stamped — the work is exactly as it was seeded
        assert work.deep_enriched_at is None


def test_enrich_deep_returns_false_for_unknown_work(db_url, monkeypatch):
    from uuid import uuid4

    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    assert two_phase.enrich_deep(uuid4()) == "missing"


def test_enrich_deep_empty_pass_still_stamps_deep_enriched_at(db_url, monkeypatch):
    """GH #97: scouts-found-nothing is NOT a failure — it's a completed pass. The timestamp
    means "the deep pass completed" (including confirmed-empty), so the requeue sweep doesn't
    treat this work as never-attempted."""
    from agentic_librarian.enrichment import two_phase

    monkeypatch.setattr(two_phase, "create_deep_scout_manager", lambda: _FakeManager(None))

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    work_id = _seed_work(manager, title="Obscure Title", author="Obscure Author")

    assert two_phase.enrich_deep(work_id) == "empty"

    with manager.get_session() as s:
        work = s.get(Work, work_id)
        assert work.deep_enriched_at is not None


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
