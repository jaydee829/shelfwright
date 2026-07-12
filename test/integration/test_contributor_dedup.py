import pytest
from sqlalchemy import create_engine

from agentic_librarian.db.models import Author, Work, WorkContributor
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import contributor_dedup as cd
from test.integration.constraint_helpers import drop_unique_indexes, recreate_unique_indexes

pytestmark = pytest.mark.db_integration

# Migration 48e3762d6c0c (this branch) adds uq_narrators_name_lower — the legacy
# contributor_dedup logic these tests pin is only meaningful in the pre-constraint prod
# window (before that migration's dedup backfill + constraint land on a real deploy), and
# test_apply_merges_dup_narrators seeds a case-only narrator dup ("Travis Baldree" vs
# "travis baldree") that the live index now rejects. uq_authors_name_lower is untouched:
# the author test's seed ("Casualfarmer" vs "Casualfarmer ") differs by trailing whitespace,
# which lower() does not collapse, so it never violates that index.
_CONTRIBUTOR_DEDUP_UNIQUE_INDEX_NAMES = ["uq_narrators_name_lower"]


@pytest.fixture(autouse=True)
def _pre_constraint_schema(db_url):
    """Drop uq_narrators_name_lower for the duration of each test (the case-dup narrator
    seed must be insertable), then restore it — mirroring the dry-run -> approve -> apply ->
    `alembic upgrade head` sequence the legacy dedup logic actually ran under in prod."""
    engine = create_engine(db_url)
    with engine.begin() as conn:
        drop_unique_indexes(conn, _CONTRIBUTOR_DEDUP_UNIQUE_INDEX_NAMES)
    yield
    with engine.begin() as conn:
        recreate_unique_indexes(conn, _CONTRIBUTOR_DEDUP_UNIQUE_INDEX_NAMES)
    engine.dispose()


def test_apply_merges_dup_authors_preserving_distinct_roles(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        a1 = Author(name="Casualfarmer")
        a2 = Author(name="Casualfarmer ")  # trailing space -> dup
        w = Work(title="Beware of Chicken")
        session.add_all([a1, a2, w])
        session.flush()
        # same person, both as Author (true dup) -> collapses to one
        session.add(WorkContributor(work_id=w.id, author_id=a1.id, role="Author"))
        session.add(WorkContributor(work_id=w.id, author_id=a2.id, role="Author"))
        # same person as Editor (distinct role) -> preserved
        session.add(WorkContributor(work_id=w.id, author_id=a2.id, role="Editor"))
        session.flush()

        cd.apply_contributor_changes(session)
        session.flush()

        assert session.query(Author).count() == 1
        survivor = session.query(Author).one()
        assert survivor.name == "Casualfarmer"  # best-cased survived
        roles = sorted(c.role for c in session.query(WorkContributor).filter_by(work_id=w.id).all())
        assert roles == ["Author", "Editor"]  # one Author (dedup) + Editor (preserved)


def test_apply_merges_dup_narrators(db_url):
    from agentic_librarian.db.models import Edition, Narrator, Work

    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        n1 = Narrator(name="Travis Baldree")
        n2 = Narrator(name="travis baldree")  # case dup
        w = Work(title="Narr Test")
        session.add_all([n1, n2, w])
        session.flush()
        e = Edition(work_id=w.id, format="audiobook", narrators=[n1, n2])
        session.add(e)
        session.flush()

        cd.apply_contributor_changes(session)
        session.flush()

        assert session.query(Narrator).count() == 1
        survivor = session.query(Narrator).one()
        assert survivor.name == "Travis Baldree"
        session.refresh(e)
        assert [n.id for n in e.narrators] == [survivor.id]  # link folded, no dup
