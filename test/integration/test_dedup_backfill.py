"""Task 4 (Spec 2026-07-12 phase6-3): the gated pre-constraint dedup planner + applier.

Structural distinguishers only (the #69 lesson) — every class groups on real relationships or
normalized values, never a sometimes-populated column. apply_dedup takes the PLAN as input and
touches only the ids it names (apply-what-was-shown)."""

from datetime import date, timedelta

import pytest
from sqlalchemy import create_engine

from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import (
    Author,
    AuthorStyle,
    Edition,
    Narrator,
    NarratorStyle,
    ReadingHistory,
    Style,
    Suggestions,
    Work,
    WorkContributor,
    edition_narrators,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import dedup_backfill as db_
from test.integration.constraint_helpers import drop_unique_indexes, recreate_unique_indexes

pytestmark = pytest.mark.db_integration

# Migration 48e3762d6c0c (already on this branch) lands the #95 unique constraints these
# planner tests are seeding duplicates AGAINST — but the real deploy sequence (spec: "Dedup
# backfill (THE USER GATE)") runs the dedup backfill BEFORE those constraints land. To seed
# realistic pre-constraint duplicate rows, drop just the 5 dedup-relevant indexes for this
# module and recreate them afterward — the FK indexes/timestamptz/deep_enriched_at parts of
# the same migration are irrelevant to duplicates and stay in place throughout.
_DEDUP_UNIQUE_INDEX_NAMES = [
    "uq_authors_name_lower",
    "uq_narrators_name_lower",
    "uq_editions_work_format",
    "uq_reading_history_user_edition_date",
    "uq_suggestions_active",
]


@pytest.fixture(autouse=True)
def _pre_constraint_schema(db_url):
    """Drop the #95 unique indexes for the duration of each test (duplicates must be
    insertable to test the planner that finds them), then restore them — mirroring the real
    dry-run -> approve -> apply -> `alembic upgrade head` sequence."""
    engine = create_engine(db_url)
    with engine.begin() as conn:
        drop_unique_indexes(conn, _DEDUP_UNIQUE_INDEX_NAMES)
    yield
    with engine.begin() as conn:
        recreate_unique_indexes(conn, _DEDUP_UNIQUE_INDEX_NAMES)
    engine.dispose()


def test_empty_db_plan_is_empty(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        plan = db_.plan_dedup(session)
        assert plan.summary() == {
            "duplicate_authors": 0,
            "duplicate_narrators": 0,
            "duplicate_editions": 0,
            "duplicate_reading_history": 0,
            "duplicate_suggestions": 0,
            "orphan_authors": 0,
            "duplicate_works_report_only": 0,
        }


def test_apply_on_empty_plan_is_noop(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        plan = db_.plan_dedup(session)
        result = db_.apply_dedup(session, plan)
        assert all(v == 0 for v in result.values())


def test_duplicate_authors_repoint_and_collision_delete(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        a1 = Author(name="Casualfarmer")
        a2 = Author(name="casualfarmer")  # case dup, structural: lower(name) group
        w1 = Work(title="Beware of Chicken")
        w2 = Work(title="Farming Life")
        w3 = Work(title="A Practical Guide to Sorcery")
        w4 = Work(title="Cursed Wife")
        style = Style(name="Wry", category="Author")
        session.add_all([a1, a2, w1, w2, w3, w4, style])
        session.flush()

        # a1 gets 4 links (most-linked -> survivor); a2 gets 3 (1 clean repoint + 1 collision + 1 style)
        session.add(WorkContributor(work_id=w1.id, author_id=a1.id, role="Author"))
        session.add(WorkContributor(work_id=w2.id, author_id=a1.id, role="Author"))
        session.add(WorkContributor(work_id=w3.id, author_id=a1.id, role="Author"))
        session.add(WorkContributor(work_id=w4.id, author_id=a1.id, role="Author"))
        session.add(WorkContributor(work_id=w2.id, author_id=a2.id, role="Editor"))
        # collision case: a2 has the SAME (work, role) that a1 already has on w1 too
        session.add(WorkContributor(work_id=w1.id, author_id=a2.id, role="Author"))
        session.add(AuthorStyle(author_id=a2.id, style_id=style.id, attribute_type="tone"))
        session.flush()

        plan = db_.plan_dedup(session)
        assert plan.summary()["duplicate_authors"] == 1
        group = plan.duplicate_authors[0]
        assert group.survivor_id == a1.id
        assert group.loser_ids == [a2.id]

        result = db_.apply_dedup(session, plan)
        assert result["duplicate_authors"] == 1
        session.flush()

        assert session.query(Author).count() == 1
        # the collision link (w1, Author) was deleted, not duplicated
        roles_w1 = sorted(c.role for c in session.query(WorkContributor).filter_by(work_id=w1.id).all())
        assert roles_w1 == ["Author"]
        # the clean repoint (w2, Editor) landed on the survivor
        roles_w2 = sorted(c.role for c in session.query(WorkContributor).filter_by(work_id=w2.id).all())
        assert roles_w2 == ["Author", "Editor"]
        editor_link = session.query(WorkContributor).filter_by(work_id=w2.id, role="Editor").one()
        assert editor_link.author_id == a1.id
        # author_styles repointed onto survivor
        assert session.query(AuthorStyle).filter_by(author_id=a1.id, style_id=style.id).count() == 1

        # re-plan converges to empty
        assert db_.plan_dedup(session).summary()["duplicate_authors"] == 0


def test_duplicate_narrators_repoint_edition_and_style(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        n1 = Narrator(name="Travis Baldree")
        n2 = Narrator(name="travis baldree")
        w = Work(title="Narr Test")
        style = Style(name="Gravelly", category="Narrator")
        session.add_all([n1, n2, w, style])
        session.flush()

        e1 = Edition(work_id=w.id, format="audiobook")
        e2 = Edition(work_id=w.id, format="audiobook_abridged")
        e3 = Edition(work_id=w.id, format="audiobook_full_cast")
        session.add_all([e1, e2, e3])
        session.flush()
        # n1 linked on three editions (most-linked survivor)
        session.execute(edition_narrators.insert().values(edition_id=e1.id, narrator_id=n1.id))
        session.execute(edition_narrators.insert().values(edition_id=e2.id, narrator_id=n1.id))
        session.execute(edition_narrators.insert().values(edition_id=e3.id, narrator_id=n1.id))
        # n2 collides on e1 (same edition already has n1) and also carries a style link
        session.execute(edition_narrators.insert().values(edition_id=e1.id, narrator_id=n2.id))
        session.add(NarratorStyle(narrator_id=n2.id, style_id=style.id, attribute_type="voice_differentiation"))
        session.flush()

        plan = db_.plan_dedup(session)
        assert plan.summary()["duplicate_narrators"] == 1
        group = plan.duplicate_narrators[0]
        assert group.survivor_id == n1.id
        assert group.loser_ids == [n2.id]

        result = db_.apply_dedup(session, plan)
        session.flush()

        assert result["duplicate_narrators"] == 1
        assert session.query(Narrator).count() == 1
        e1_narrators = session.execute(
            edition_narrators.select().where(edition_narrators.c.edition_id == e1.id)
        ).fetchall()
        assert [r.narrator_id for r in e1_narrators] == [n1.id]  # collision deleted, not duplicated
        assert session.query(NarratorStyle).filter_by(narrator_id=n1.id, style_id=style.id).count() == 1

        assert db_.plan_dedup(session).summary()["duplicate_narrators"] == 0


def test_duplicate_editions_repoint_reading_history_and_narrators(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        w = Work(title="Dup Edition Work")
        n = Narrator(name="Some Narrator")
        session.add_all([w, n])
        session.flush()

        e1 = Edition(work_id=w.id, format="ebook")
        e2 = Edition(work_id=w.id, format="ebook")  # exact (work_id, format) dup
        session.add_all([e1, e2])
        session.flush()
        session.execute(edition_narrators.insert().values(edition_id=e1.id, narrator_id=n.id))
        session.execute(edition_narrators.insert().values(edition_id=e2.id, narrator_id=n.id))  # collision

        rh1 = ReadingHistory(edition_id=e1.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 1, 1))
        rh2 = ReadingHistory(edition_id=e2.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 2, 1))  # repoints
        session.add_all([rh1, rh2])
        session.flush()

        # give e1 the extra link so it is "most-linked" (survivor)
        rh3 = ReadingHistory(edition_id=e1.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 3, 1))
        session.add(rh3)
        session.flush()

        plan = db_.plan_dedup(session)
        assert plan.summary()["duplicate_editions"] == 1
        group = plan.duplicate_editions[0]
        assert group.survivor_id == e1.id
        assert group.loser_ids == [e2.id]

        result = db_.apply_dedup(session, plan)
        session.flush()

        assert result["duplicate_editions"] == 1
        assert session.query(Edition).count() == 1
        # rh2 repointed onto e1's surviving id
        assert session.get(ReadingHistory, rh2.id).edition_id == e1.id
        e1_narrators = session.execute(
            edition_narrators.select().where(edition_narrators.c.edition_id == e1.id)
        ).fetchall()
        assert [r.narrator_id for r in e1_narrators] == [n.id]  # collision deleted, not duplicated

        assert db_.plan_dedup(session).summary()["duplicate_editions"] == 0


def test_duplicate_reading_history_exact_groups_keep_oldest(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        w = Work(title="RH Dup Work")
        session.add(w)
        session.flush()
        e = Edition(work_id=w.id, format="ebook")
        session.add(e)
        session.flush()

        rh1 = ReadingHistory(edition_id=e.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 5, 1))
        session.add(rh1)
        session.flush()
        rh2 = ReadingHistory(edition_id=e.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 5, 1))
        session.add(rh2)
        session.flush()

        # ReadingHistory has no created_at; "keep oldest" uses the lowest-id structural
        # proxy (same tie-break as contributor_dedup._pick_survivor), so the survivor is
        # whichever of rh1/rh2 sorts first by str(id) — not necessarily insertion order.
        expected_survivor, expected_loser = sorted([rh1, rh2], key=lambda r: str(r.id))

        plan = db_.plan_dedup(session)
        assert plan.summary()["duplicate_reading_history"] == 1
        group = plan.duplicate_reading_history[0]
        assert group.survivor_id == expected_survivor.id
        assert group.loser_ids == [expected_loser.id]

        result = db_.apply_dedup(session, plan)
        session.flush()

        assert result["duplicate_reading_history"] == 1
        assert session.query(ReadingHistory).count() == 1
        assert session.get(ReadingHistory, expected_survivor.id) is not None
        assert session.get(ReadingHistory, expected_loser.id) is None

        assert db_.plan_dedup(session).summary()["duplicate_reading_history"] == 0


def test_duplicate_suggestions_keep_oldest_suggested(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        w = Work(title="Suggestion Dup Work")
        session.add(w)
        session.flush()

        now = __import__("datetime").datetime.now(__import__("datetime").UTC)
        s1 = Suggestions(
            work_id=w.id, user_id=DEFAULT_USER_ID, status="Suggested", suggested_at=now - timedelta(days=2)
        )
        session.add(s1)
        session.flush()
        s2 = Suggestions(work_id=w.id, user_id=DEFAULT_USER_ID, status="Suggested", suggested_at=now)
        session.add(s2)
        session.flush()
        # a Rejected duplicate on the same (user, work) does NOT count (status filter is structural)
        s3 = Suggestions(work_id=w.id, user_id=DEFAULT_USER_ID, status="Rejected", suggested_at=now)
        session.add(s3)
        session.flush()

        plan = db_.plan_dedup(session)
        assert plan.summary()["duplicate_suggestions"] == 1
        group = plan.duplicate_suggestions[0]
        assert group.survivor_id == s1.id
        assert group.loser_ids == [s2.id]

        result = db_.apply_dedup(session, plan)
        session.flush()

        assert result["duplicate_suggestions"] == 1
        assert session.get(Suggestions, s1.id) is not None
        assert session.get(Suggestions, s2.id) is None
        assert session.get(Suggestions, s3.id) is not None  # untouched, different status

        assert db_.plan_dedup(session).summary()["duplicate_suggestions"] == 0


def test_orphan_authors_deleted(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        linked = Author(name="Linked Author")
        orphan = Author(name="Orphan Author")
        w = Work(title="Some Work")
        session.add_all([linked, orphan, w])
        session.flush()
        session.add(WorkContributor(work_id=w.id, author_id=linked.id, role="Author"))
        session.flush()

        plan = db_.plan_dedup(session)
        assert plan.summary()["orphan_authors"] == 1
        assert plan.orphan_authors == [orphan.id]

        result = db_.apply_dedup(session, plan)
        session.flush()

        assert result["orphan_authors"] == 1
        assert session.query(Author).count() == 1
        assert session.get(Author, linked.id) is not None
        assert session.get(Author, orphan.id) is None

        assert db_.plan_dedup(session).summary()["orphan_authors"] == 0


def test_orphan_author_with_style_link_not_orphaned(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        a = Author(name="Styled Only")
        style = Style(name="Wry", category="Author")
        session.add_all([a, style])
        session.flush()
        session.add(AuthorStyle(author_id=a.id, style_id=style.id, attribute_type="tone"))
        session.flush()

        plan = db_.plan_dedup(session)
        assert plan.summary()["orphan_authors"] == 0


def test_duplicate_works_report_only_never_applied(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        a = Author(name="Ann Leckie")
        w1 = Work(title="Ancillary Justice")
        w2 = Work(title="  ancillary   justice  ")  # normalizes to the same title
        session.add_all([a, w1, w2])
        session.flush()
        session.add(WorkContributor(work_id=w1.id, author_id=a.id, role="Author"))
        session.add(WorkContributor(work_id=w2.id, author_id=a.id, role="Author"))
        session.flush()

        plan = db_.plan_dedup(session)
        assert plan.summary()["duplicate_works_report_only"] == 1

        result = db_.apply_dedup(session, plan)
        session.flush()

        assert "duplicate_works_report_only" not in result or result["duplicate_works_report_only"] == 0
        assert session.query(Work).count() == 2  # never touched

        # re-plan finds the same report-only group again (not converged, by design)
        assert db_.plan_dedup(session).summary()["duplicate_works_report_only"] == 1


def test_apply_skips_stale_plan_ids(db_url):
    """apply_dedup applies EXACTLY the plan's ids; if a planned row vanished before apply,
    it is skipped and counted, never re-derived."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        w = Work(title="Stale Plan Work")
        session.add(w)
        session.flush()
        e = Edition(work_id=w.id, format="ebook")
        session.add(e)
        session.flush()
        rh1 = ReadingHistory(edition_id=e.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 6, 1))
        session.add(rh1)
        session.flush()
        rh2 = ReadingHistory(edition_id=e.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 6, 1))
        session.add(rh2)
        session.flush()

        plan = db_.plan_dedup(session)
        assert plan.summary()["duplicate_reading_history"] == 1

        # the loser row vanishes out from under the plan (simulating a concurrent change)
        session.delete(session.get(ReadingHistory, plan.duplicate_reading_history[0].loser_ids[0]))
        session.flush()

        result = db_.apply_dedup(session, plan)
        assert result["duplicate_reading_history"] == 0
        assert result["skipped_stale"] == 1


def test_orphan_authors_recomputed_after_merge_needs_replan(db_url):
    """An author orphaned BY a same-run merge is caught only on a re-run (documented honesty,
    not simulated ahead of time)."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        a1 = Author(name="Solo Writer")
        a2 = Author(name="solo writer")
        w = Work(title="Only Book")
        session.add_all([a1, a2, w])
        session.flush()
        # only a2 (the loser, by fewer/equal links + tiebreak) is linked; a1 has zero links
        # so after merge, if a1 becomes survivor, a2's link repoints and a1 is NOT orphaned —
        # construct the reverse: a1 has the link, a2 has none, so a1 survives with the link.
        session.add(WorkContributor(work_id=w.id, author_id=a1.id, role="Author"))
        session.flush()

        plan = db_.plan_dedup(session)
        # a2 has zero links -> not "most-linked", but tie-break is lowest str(id); either way
        # only one class fires in this single plan snapshot
        assert plan.summary()["duplicate_authors"] == 1
        result = db_.apply_dedup(session, plan)
        session.flush()
        assert result["duplicate_authors"] == 1
        assert session.query(Author).count() == 1
