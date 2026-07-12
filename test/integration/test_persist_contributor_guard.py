import pytest

from agentic_librarian.db.models import Author, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.persist import persist_enriched_work

pytestmark = pytest.mark.db_integration


class _NullManager:
    def standardize_trope(self, *a, **k):
        return None

    def standardize_style(self, *a, **k):
        return None


def test_persist_dedups_same_author_twice(db_url):
    manager = DatabaseManager(db_url)
    row = {
        "Title": "Guard Test",
        "format": "ebook",
        "contributors": [
            {"name": "Casualfarmer", "role": "Author"},
            {"name": "Casualfarmer ", "role": "Author"},  # whitespace dup -> one row
            {"name": "Casualfarmer", "role": "Editor"},  # distinct role -> kept
        ],
        "skip_enrichment": True,
    }
    with manager.get_session() as session:
        work = persist_enriched_work(session, row, _NullManager(), _NullManager())
        session.flush()
        roles = sorted(c.role for c in session.query(WorkContributor).filter_by(work_id=work.id).all())
        assert roles == ["Author", "Editor"]


def test_persist_reuses_existing_author_case_insensitively(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        session.add(Author(name="Casualfarmer"))  # pre-existing, Title-cased
        session.flush()
        row = {
            "Title": "Case Reuse Test",
            "format": "ebook",
            "contributors": [{"name": "casualfarmer", "role": "Author"}],  # lower-cased on the way in
            "skip_enrichment": True,
        }
        persist_enriched_work(session, row, _NullManager(), _NullManager())
        session.flush()
        # the existing row is reused, not duplicated
        assert session.query(Author).filter(Author.name.ilike("casualfarmer")).count() == 1


def test_existing_work_gains_new_contributor(db_url):
    """#96: re-persisting an existing work links newly discovered contributors."""
    manager = DatabaseManager(db_url)
    row1 = {
        "Title": "CG Work",
        "Author_1": "Alice",
        "format": "ebook",
        "skip_enrichment": False,
        "date_completed": None,
        "genres": [],
        "moods": [],
    }
    with manager.get_session() as s:
        persist_enriched_work(s, row1, _NullManager(), _NullManager())
        s.flush()
    with manager.get_session() as s:
        row2 = dict(row1)
        row2["contributors"] = [{"name": "Alice", "role": "Author"}, {"name": "Bob", "role": "Author"}]
        persist_enriched_work(s, row2, _NullManager(), _NullManager())
        s.flush()
        work = s.query(Work).filter_by(title="CG Work").one()
        assert {(c.author.name, c.role) for c in work.contributors} == {("Alice", "Author"), ("Bob", "Author")}
