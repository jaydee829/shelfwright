import pytest

from agentic_librarian.db.models import WorkContributor
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
