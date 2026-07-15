"""db_integration tests for the works-merge APPLY step (H2, Spec 2026-07-14 "Merge
composition" / "Gate" / item 6): compose_cluster_merge (READ ONLY composition), the op-tagged
token gate (works_merge_tokens / works_merge_delta), the report round-trip
(write_works_merge_apply_report / parse_works_merge_report), and apply_works_merge itself (THE
USER GATE — re-plans fresh, refuses on drift, executes in one transaction).

Kept as a sibling to test/integration/test_works_merge.py (H1's detection-only tests) rather
than extending that file: H1 asserts purely on plan_works_merge's read-only detection output;
this file additionally seeds mutable child rows (editions, reading_history, suggestions,
work_tropes, work_styles, work_contributors, detected_duplicates) and asserts on real DB
mutation, so keeping the seeding/assertion shapes separate avoids the two concerns bleeding
into one giant fixture file.

House rule: case-driven tests are parametrized (pytest.mark.parametrize), never a for-loop
inside a single test body."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import uuid4

import pytest

from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import (
    Author,
    DetectedDuplicate,
    Edition,
    ReadingHistory,
    Style,
    Suggestions,
    Trope,
    User,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import dedup_backfill as db_

pytestmark = pytest.mark.db_integration


def _work(title: str, *, deep_enriched_at=None) -> Work:
    return Work(title=title, deep_enriched_at=deep_enriched_at)


def _second_user(session) -> User:
    """DEFAULT_USER_ID is auto-seeded by conftest's autouse fixture; multi-user
    reading_history/suggestions collision tests need a SECOND, distinct user row."""
    user = User(email=f"{uuid4()}@example.test")
    session.add(user)
    session.flush()
    return user


def test_lone_loser_edition_repoints_onto_survivor_no_collision(db_url):
    """Simplest composition shape: survivor has no edition at all, loser has one ebook
    edition. compose_cluster_merge must plan a whole-edition repoint (no format collision to
    resolve), never an edition merge."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Beware of Chicken", deep_enriched_at=datetime(2026, 6, 12, tzinfo=UTC))
        loser = _work("Beware of Chicken")
        session.add_all([survivor, loser])
        session.flush()
        loser_edition = Edition(work_id=loser.id, isbn_13="9781039452275", format="ebook")
        session.add(loser_edition)
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_isbn",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.survivor_id == survivor.id
        assert comp.loser_ids == [loser.id]
        assert comp.repoint_edition_ids == [loser_edition.id]
        assert comp.merge_editions == []
        assert comp.dropped_duplicate_reads == 0


@pytest.mark.parametrize(
    "case_name, fmt",
    [
        ("named_format_collision", "ebook"),
        ("null_format_collision_nulls_not_distinct", None),
    ],
)
def test_edition_format_collision_merges_instead_of_repointing(db_url, case_name, fmt):
    """uq_editions_work_format collision (including the NULLS-NOT-DISTINCT NULL-to-NULL case,
    per the CLAUDE.md brief): the loser edition must NOT repoint — it must be folded into an
    EditionMergeGroup targeting the survivor's existing same-format edition."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        survivor_edition = Edition(work_id=survivor.id, format=fmt)
        loser_edition = Edition(work_id=loser.id, format=fmt)
        session.add_all([survivor_edition, loser_edition])
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.repoint_edition_ids == []
        assert len(comp.merge_editions) == 1
        mg = comp.merge_editions[0]
        assert mg.survivor_id == survivor_edition.id
        assert mg.loser_ids == [loser_edition.id]


def test_edition_merge_repoints_reading_history_no_collision(db_url):
    """A read event on the loser edition, no colliding date on the survivor's edition -> the
    read is repointed onto the survivor's edition, never dropped."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        survivor_edition = Edition(work_id=survivor.id, format="ebook")
        loser_edition = Edition(work_id=loser.id, format="ebook")
        session.add_all([survivor_edition, loser_edition])
        session.flush()
        rh = ReadingHistory(edition_id=loser_edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 1, 1))
        session.add(rh)
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        mg = comp.merge_editions[0]
        assert mg.repoint_reading_history == [rh.id]
        assert mg.delete_reading_history == []
        assert comp.dropped_duplicate_reads == 0


def test_edition_merge_drops_duplicate_read_same_user_edition_date(db_url):
    """The exact same read event recorded on both works (same user, same date_completed) lands
    on the same survivor edition once repointed -> keep the survivor's pre-existing row, drop
    the loser's, count it under dropped_duplicate_reads. Never silently lose OR duplicate a read."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        survivor_edition = Edition(work_id=survivor.id, format="ebook")
        loser_edition = Edition(work_id=loser.id, format="ebook")
        session.add_all([survivor_edition, loser_edition])
        session.flush()
        survivor_rh = ReadingHistory(
            edition_id=survivor_edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 1, 1)
        )
        loser_rh = ReadingHistory(edition_id=loser_edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 1, 1))
        session.add_all([survivor_rh, loser_rh])
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        mg = comp.merge_editions[0]
        assert mg.repoint_reading_history == []
        assert mg.delete_reading_history == [loser_rh.id]
        assert comp.dropped_duplicate_reads == 1


def test_edition_merge_drops_duplicate_narrator_link_keeps_repoints_new(db_url):
    """edition_narrators PK collision (survivor edition already has this narrator) -> drop the
    loser's link (no history to lose, it's a plain link); a DIFFERENT narrator on the loser
    edition repoints cleanly."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        from agentic_librarian.db.models import Narrator, edition_narrators

        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        survivor_edition = Edition(work_id=survivor.id, format="audiobook")
        loser_edition = Edition(work_id=loser.id, format="audiobook")
        session.add_all([survivor_edition, loser_edition])
        session.flush()
        shared_narrator = Narrator(name="Michael Kramer")
        new_narrator = Narrator(name="Someone Else")
        session.add_all([shared_narrator, new_narrator])
        session.flush()
        session.execute(
            edition_narrators.insert().values(edition_id=survivor_edition.id, narrator_id=shared_narrator.id)
        )
        session.execute(edition_narrators.insert().values(edition_id=loser_edition.id, narrator_id=shared_narrator.id))
        session.execute(edition_narrators.insert().values(edition_id=loser_edition.id, narrator_id=new_narrator.id))
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        mg = comp.merge_editions[0]
        assert set(mg.repoint_narrators) == {(loser_edition.id, new_narrator.id)}
        assert set(mg.delete_narrators) == {(loser_edition.id, shared_narrator.id)}


# --------------------------------------------------------------------------------------------
# 2. Suggestions
# --------------------------------------------------------------------------------------------


def test_suggestion_repoints_when_no_active_collision(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        s = Suggestions(work_id=loser.id, user_id=DEFAULT_USER_ID, status="Suggested")
        session.add(s)
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.repoint_suggestion_ids == [s.id]
        assert comp.drop_duplicate_suggestion_ids == []


def test_suggestion_drops_duplicate_on_active_collision(db_url):
    """uq_suggestions_active collision (same user, both work_ids -> same survivor work_id):
    keep the survivor's pre-existing active suggestion, drop the loser's."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        survivor_s = Suggestions(work_id=survivor.id, user_id=DEFAULT_USER_ID, status="Suggested")
        loser_s = Suggestions(work_id=loser.id, user_id=DEFAULT_USER_ID, status="Suggested")
        session.add_all([survivor_s, loser_s])
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.repoint_suggestion_ids == []
        assert comp.drop_duplicate_suggestion_ids == [loser_s.id]


def test_non_suggested_status_always_repoints_never_drops(db_url):
    """Accepted/Rejected suggestions carry no active-uniqueness constraint — always repoint,
    even if the survivor already has an Accepted/Rejected row for that user."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        survivor_s = Suggestions(work_id=survivor.id, user_id=DEFAULT_USER_ID, status="Accepted")
        loser_s = Suggestions(work_id=loser.id, user_id=DEFAULT_USER_ID, status="Accepted")
        session.add_all([survivor_s, loser_s])
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.repoint_suggestion_ids == [loser_s.id]
        assert comp.drop_duplicate_suggestion_ids == []


# --------------------------------------------------------------------------------------------
# 3. Trope / style link union
# --------------------------------------------------------------------------------------------


def test_trope_link_union_copies_missing_drops_existing(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        shared_trope = Trope(name="Found Family")
        new_trope = Trope(name="Chosen One")
        session.add_all([shared_trope, new_trope])
        session.flush()
        session.add(WorkTrope(work_id=survivor.id, trope_id=shared_trope.id, justification="scout said so"))
        session.add(WorkTrope(work_id=loser.id, trope_id=shared_trope.id, justification="also scout"))
        session.add(
            WorkTrope(work_id=loser.id, trope_id=new_trope.id, relevance_score=0.9, justification="unique to loser")
        )
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.drop_trope_links == [(loser.id, shared_trope.id)]
        assert comp.copy_trope_links == [(loser.id, new_trope.id, 0.9, "unique to loser")]


def test_style_link_union_copies_missing_drops_existing(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        shared_style = Style(name="Fast-paced", category="Work")
        new_style = Style(name="Introspective", category="Work")
        session.add_all([shared_style, new_style])
        session.flush()
        session.add(WorkStyle(work_id=survivor.id, style_id=shared_style.id, attribute_type="pacing"))
        session.add(WorkStyle(work_id=loser.id, style_id=shared_style.id, attribute_type="pacing"))
        session.add(WorkStyle(work_id=loser.id, style_id=new_style.id, attribute_type="tone"))
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.drop_style_links == [(loser.id, shared_style.id, "pacing")]
        assert comp.copy_style_links == [(loser.id, new_style.id, "tone")]


# --------------------------------------------------------------------------------------------
# 4. Contributors: union by (author_id, role) + the #142 malformed-author carve-out
# --------------------------------------------------------------------------------------------


def test_contributor_union_copies_new_author_role_drops_exact_duplicate(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        shared_author = Author(name="Brandon Sanderson")
        new_author = Author(name="Someone Else")
        session.add_all([shared_author, new_author])
        session.flush()
        session.add(WorkContributor(work_id=survivor.id, author_id=shared_author.id, role="Author"))
        session.add(WorkContributor(work_id=loser.id, author_id=shared_author.id, role="Author"))
        session.add(WorkContributor(work_id=loser.id, author_id=new_author.id, role="Author"))
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.drop_contributors == [(loser.id, shared_author.id, "Author")]
        assert comp.copy_contributors == [(new_author.id, "Author")]
        assert comp.malformed_author_candidates == []


def test_malformed_author_casefold_duplicate_not_copied_reported_only(db_url):
    """#142: a DIFFERENT Author row on the loser case-folds equal (after stripping) to an
    Author already contributing to the survivor under a DIFFERENT author_id -> do NOT copy the
    link, report the author id under malformed_author_candidates, never mutate the Author row.

    The two names must be DB-distinct under uq_authors_name_lower (a raw `lower(name)` unique
    index, no strip/casefold — the two rows in this test really can coexist on a real prod
    schema) while still comparing equal under this module's own `.strip().casefold()` check —
    a leading-space artifact (the realistic #142 shape: dirty import data) does exactly that:
    lower(' casualfarmer') != lower('casualfarmer') at the DB level, but
    ' CasualFarmer'.strip().casefold() == 'CasualFarmer'.strip().casefold()."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Beware of Chicken")
        loser = _work("Beware of Chicken")
        session.add_all([survivor, loser])
        session.flush()
        clean_author = Author(name="CasualFarmer")
        dirty_author = Author(name=" CasualFarmer")  # leading-space artifact, DB-distinct
        session.add_all([clean_author, dirty_author])
        session.flush()
        session.add(WorkContributor(work_id=survivor.id, author_id=clean_author.id, role="Author"))
        session.add(WorkContributor(work_id=loser.id, author_id=dirty_author.id, role="Author"))
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_isbn",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.copy_contributors == []
        assert comp.drop_contributors == []
        assert comp.malformed_author_candidates == [dirty_author.id]

        # No Author mutation whatsoever — report-only.
        assert session.get(Author, dirty_author.id) is not None
        assert session.get(Author, dirty_author.id).name == " CasualFarmer"


# --------------------------------------------------------------------------------------------
# 5. detected_duplicates: deleted before Work rows, unconditional on loser-id match either side
# --------------------------------------------------------------------------------------------


def test_detected_duplicate_rows_referencing_loser_are_planned_for_deletion(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Calling Bullshit: The Art of Skepticism")
        loser = _work("Calling Bullshit")
        session.add_all([survivor, loser])
        session.flush()
        session.add(DetectedDuplicate(work_id_a=loser.id, work_id_b=survivor.id, source="deep_pass_redirect"))
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_detected_duplicates",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.delete_detection_pairs == [(loser.id, survivor.id)]


def test_detected_duplicate_row_against_unrelated_work_still_planned_for_deletion(db_url):
    """A loser named in a detection row against a work id OUTSIDE this cluster (a stale
    detection) is still planned for deletion — unconditional on loser-id match, either side,
    per the spec's item 6."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Calling Bullshit: The Art of Skepticism")
        loser = _work("Calling Bullshit")
        unrelated = _work("Some Unrelated Book")
        session.add_all([survivor, loser, unrelated])
        session.flush()
        session.add(DetectedDuplicate(work_id_a=loser.id, work_id_b=unrelated.id, source="deep_pass_redirect"))
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser.id],
            titles=[survivor.title, loser.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert comp.delete_detection_pairs == [(loser.id, unrelated.id)]


# --------------------------------------------------------------------------------------------
# 6. Loser Work rows deleted last (composition-level: just the planned id list)
# --------------------------------------------------------------------------------------------


def test_delete_work_ids_is_exactly_the_loser_set(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn")
        loser1 = _work("Mistborn")
        loser2 = _work("Mistborn")
        session.add_all([survivor, loser1, loser2])
        session.flush()

        cluster = db_.WorksMergeCluster(
            class_name="works_same_identity",
            work_ids=[survivor.id, loser1.id, loser2.id],
            titles=[survivor.title, loser1.title, loser2.title],
            survivor_id=survivor.id,
        )
        comp = db_.compose_cluster_merge(session, cluster)

        assert set(comp.delete_work_ids) == {loser1.id, loser2.id}
        assert survivor.id not in comp.delete_work_ids
