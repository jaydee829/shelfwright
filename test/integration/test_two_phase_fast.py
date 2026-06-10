import pytest

from agentic_librarian.db.models import Edition, Work
from agentic_librarian.db.session import DatabaseManager

pytestmark = pytest.mark.db_integration


class _FakeManager:
    """Stands in for a real ScoutManager: returns a fixed fast-pass metadata dict."""

    def __init__(self, result):
        self._result = result

    def enrich(self, title, author, format="Paperback", **kwargs):
        return self._result


def test_enrich_fast_persists_new_work_and_reports_created(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    fixed = {
        "title": "Project Hail Mary",
        "contributors": [{"name": "Andy Weir", "role": "Author"}],
        "genres": ["Sci-Fi"],
        "moods": [],
        "isbn_13": "9780593135204",
    }
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: _FakeManager(fixed))

    work_id, created = two_phase.enrich_fast("Project Hail Mary", "Andy Weir", "ebook")

    assert created is True
    with manager.get_session() as s:
        work = s.get(Work, work_id)
        assert work is not None and work.title == "Project Hail Mary"
        edition = s.query(Edition).filter_by(work_id=work_id, format="ebook").first()
        assert edition is not None


def test_enrich_fast_dedups_existing_work_without_rescouting(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    fixed = {"title": "Dune", "contributors": [{"name": "Frank Herbert", "role": "Author"}],
             "genres": [], "moods": []}
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: _FakeManager(fixed))

    first_id, first_created = two_phase.enrich_fast("Dune", "Frank Herbert", "ebook")
    second_id, second_created = two_phase.enrich_fast("  dune ", "FRANK HERBERT", "ebook")

    assert first_created is True
    assert second_created is False  # normalized title+author matched the existing work
    assert first_id == second_id


def test_enrich_fast_returns_none_when_scouts_find_nothing(db_url, monkeypatch):
    from agentic_librarian.enrichment import two_phase

    manager = DatabaseManager(db_url)
    monkeypatch.setattr(two_phase, "db_manager", manager)
    monkeypatch.setattr(two_phase, "create_fast_scout_manager", lambda: _FakeManager({}))

    assert two_phase.enrich_fast("Nonexistent", "Nobody", "ebook") is None
