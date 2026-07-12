"""PR-D migration: constraints, indexes, timestamptz, deep_enriched_at (#95 #97 #108 #109)."""

import pytest
from sqlalchemy import inspect, text

pytestmark = pytest.mark.db_integration


def test_unique_indexes_exist(db_url):
    from agentic_librarian.db.session import DatabaseManager

    insp = inspect(DatabaseManager(db_url).engine)
    author_uniques = {i["name"] for i in insp.get_indexes("authors") if i.get("unique")}
    assert "uq_authors_name_lower" in author_uniques
    edition_uniques = {i["name"] for i in insp.get_indexes("editions") if i.get("unique")}
    assert "uq_editions_work_format" in edition_uniques
    rh = {i["name"] for i in insp.get_indexes("reading_history") if i.get("unique")}
    assert "uq_reading_history_user_edition_date" in rh
    sugg = {i["name"] for i in insp.get_indexes("suggestions") if i.get("unique")}
    assert "uq_suggestions_active" in sugg


def test_fk_indexes_exist(db_url):
    from agentic_librarian.db.session import DatabaseManager

    insp = inspect(DatabaseManager(db_url).engine)
    for table, col in [
        ("editions", "work_id"),
        ("reading_history", "edition_id"),
        ("work_tropes", "trope_id"),
        ("work_contributors", "author_id"),
        ("suggestions", "work_id"),
        ("author_styles", "style_id"),
        ("work_styles", "style_id"),
        ("usage", "conversation_id"),
        ("narrator_styles", "style_id"),
        ("edition_narrators", "narrator_id"),
    ]:
        names = {i["name"] for i in insp.get_indexes(table)}
        assert f"ix_{table}_{col}" in names, f"missing ix_{table}_{col}"


def test_timestamps_are_timestamptz(db_url):
    from agentic_librarian.db.session import DatabaseManager

    m = DatabaseManager(db_url)
    with m.get_session() as s:
        rows = s.execute(
            text(
                "SELECT table_name, column_name FROM information_schema.columns "
                "WHERE data_type = 'timestamp without time zone' AND table_schema = 'public'"
            )
        ).all()
    assert rows == [], f"still naive: {rows}"


def test_works_deep_enriched_at(db_url):
    from agentic_librarian.db.session import DatabaseManager

    insp = inspect(DatabaseManager(db_url).engine)
    cols = {c["name"] for c in insp.get_columns("works")}
    assert "deep_enriched_at" in cols


def test_deep_enriched_at_backfill_semantics(db_url):
    """The migration's own `op.execute` backfill (works.deep_enriched_at = now() WHERE the
    work has any work_tropes row) can't be exercised directly here: this suite's
    `_create_test_database` fixture (test/conftest.py) runs `alembic upgrade head` against
    an EMPTY database, so by the time this migration's upgrade() executes, `work_tropes` has
    no rows for it to match — there's no way to seed a work+trope pair BEFORE the migration
    runs within this test chain. What's actually verifiable post-migration is the semantics
    contract the backfill establishes going forward: a work seeded WITH a trope link and no
    explicit stamp behaves like a work the migration's backfill would have stamped (picked
    up by plan_requeue only under "no_real_trope" if its tropes are all fallback, never under
    "never_deep_enriched"), while a work with zero trope links and no stamp is
    "never_deep_enriched" — exactly the reason class the runbook says the first sweep should
    now report only real gaps for. See test/integration/test_enrichment_sweep.py for the
    plan_requeue-level assertions of this contract, and the migration file's inline comment /
    docs/runbooks/phase6-3-schema-rollout.md for the backfill itself."""
    from datetime import UTC, datetime

    from agentic_librarian.db.models import Trope, Work, WorkTrope
    from agentic_librarian.db.session import DatabaseManager
    from agentic_librarian.etl.enrichment_sweep import plan_requeue

    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        # Simulates what the migration's backfill stamps: a work with a real trope link,
        # deep_enriched_at set (as the backfill would set it).
        stamped = Work(title="Backfill-Equivalent Work", deep_enriched_at=datetime.now(UTC))
        s.add(stamped)
        s.flush()
        trope = Trope(name="Found Family")
        s.add(trope)
        s.flush()
        s.add(WorkTrope(work_id=stamped.id, trope_id=trope.id))

        # A work the backfill correctly leaves NULL: zero trope links.
        never_touched = Work(title="No Trope Links At All", deep_enriched_at=None)
        s.add(never_touched)
        s.flush()
        stamped_id, never_touched_id = stamped.id, never_touched.id

    with manager.get_session() as s:
        plan = plan_requeue(s)

    by_id = {c.work_id: c for c in plan}
    assert stamped_id not in by_id  # backfill-equivalent stamp + real trope -> excluded
    assert by_id[never_touched_id].reason == "never_deep_enriched"
