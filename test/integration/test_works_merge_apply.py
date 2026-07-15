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
from sqlalchemy import select

from agentic_librarian.core.user_context import DEFAULT_USER_ID
from agentic_librarian.db.models import (
    Author,
    DetectedDuplicate,
    Edition,
    Narrator,
    ReadingHistory,
    Style,
    Suggestions,
    Trope,
    User,
    Work,
    WorkContributor,
    WorkStyle,
    WorkTrope,
    edition_narrators,
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


# --------------------------------------------------------------------------------------------
# 7. apply_works_merge itself — THE USER GATE. plan -> report -> apply, driven through the real
# functions end to end (mirrors test/integration/test_fallback_repair.py's e2e shapes).
# --------------------------------------------------------------------------------------------


def test_beware_shaped_full_e2e_through_apply(db_url):
    """The brief's canonical shape: two works sharing an ISBN AND edition format (works_same_isbn
    class, ALSO an edition-format collision -> merge_edition), reads split across two users plus
    one user with reads on BOTH works (one date collides with an existing survivor read, one is
    distinct), an active suggestion on the loser, richer survivor tropes plus one loser-only
    trope (relevance/justification carried), and detected_duplicates rows in BOTH orders.
    Asserts: one work survives; every read is preserved or counted dropped; the suggestion is
    repointed; the trope union is exact; both detection rows are gone; the EXACT per-op counts
    map (including merge_edition >= 1); the orphan-author pointer; and a fresh re-plan converges
    to zero clusters."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        second_user = _second_user(session)

        survivor = _work("Beware of Chicken", deep_enriched_at=datetime(2026, 6, 12, tzinfo=UTC))
        loser = _work("Beware of Chicken")
        session.add_all([survivor, loser])
        session.flush()

        survivor_edition = Edition(work_id=survivor.id, isbn_13="9781039452275", format="ebook")
        loser_edition = Edition(work_id=loser.id, isbn_13="9781039452275", format="ebook")
        session.add_all([survivor_edition, loser_edition])
        session.flush()

        # second_user: one read, only on the loser -> distinct -> repoint.
        session.add(
            ReadingHistory(edition_id=loser_edition.id, user_id=second_user.id, date_completed=date(2024, 3, 1))
        )
        # DEFAULT_USER_ID: a read on the survivor, AND two reads on the loser — one whose date
        # collides with the survivor's own read (dropped-as-duplicate), one whose date is
        # distinct (repointed).
        session.add(
            ReadingHistory(edition_id=survivor_edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2023, 1, 1))
        )
        session.add(
            ReadingHistory(edition_id=loser_edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2023, 1, 1))
        )
        session.add(
            ReadingHistory(edition_id=loser_edition.id, user_id=DEFAULT_USER_ID, date_completed=date(2024, 5, 5))
        )
        session.flush()

        session.add(Suggestions(work_id=loser.id, user_id=second_user.id, status="Suggested"))
        session.flush()

        survivor_trope_1 = Trope(name="Found Family")
        survivor_trope_2 = Trope(name="Chosen One")
        loser_only_trope = Trope(name="Slow Burn")
        session.add_all([survivor_trope_1, survivor_trope_2, loser_only_trope])
        session.flush()
        session.add(WorkTrope(work_id=survivor.id, trope_id=survivor_trope_1.id, justification="scout said so"))
        session.add(WorkTrope(work_id=survivor.id, trope_id=survivor_trope_2.id, justification="scout said so too"))
        session.add(
            WorkTrope(
                work_id=loser.id,
                trope_id=loser_only_trope.id,
                relevance_score=0.75,
                justification="loser-only context",
            )
        )
        session.flush()

        session.add(DetectedDuplicate(work_id_a=loser.id, work_id_b=survivor.id, source="deep_pass_redirect"))
        session.add(DetectedDuplicate(work_id_a=survivor.id, work_id_b=loser.id, source="deep_pass_redirect"))
        session.flush()

        # --- plan + report ---
        clusters = db_.plan_works_merge(session)
        assert clusters.summary()["works_same_isbn"] == 1
        compositions = [db_.compose_cluster_merge(session, c) for c in db_.applyable_works_merge_clusters(clusters)]
        report_path = db_.write_works_merge_apply_report(clusters, compositions)

        # --- apply ---
        applied = db_.apply_works_merge(session, report_path)
        session.flush()

        assert applied == {
            "repoint_edition": 0,
            "merge_edition": 1,
            "repoint_read": 2,
            "drop_duplicate_read": 1,
            "repoint_narrator": 0,
            "drop_narrator": 0,
            "repoint_suggestion": 1,
            "drop_duplicate_suggestion": 0,
            "copy_link": 1,
            "drop_link": 0,
            "copy_contributor": 0,
            "drop_contributor": 0,
            "delete_detection": 2,
            "delete_work": 1,
            "skipped_stale": 0,
            "orphaned_authors_pointer": 0,
        }

        assert session.query(Work).count() == 1
        assert session.get(Work, survivor.id) is not None
        assert session.get(Work, loser.id) is None
        assert session.get(Edition, loser_edition.id) is None

        remaining_reads = session.query(ReadingHistory).all()
        assert len(remaining_reads) == 3  # one dropped-as-duplicate, none silently lost
        assert all(rh.edition_id == survivor_edition.id for rh in remaining_reads)
        read_keys = {(rh.user_id, rh.date_completed) for rh in remaining_reads}
        assert read_keys == {
            (second_user.id, date(2024, 3, 1)),
            (DEFAULT_USER_ID, date(2023, 1, 1)),
            (DEFAULT_USER_ID, date(2024, 5, 5)),
        }

        suggestion = session.query(Suggestions).filter_by(user_id=second_user.id).one()
        assert suggestion.work_id == survivor.id

        survivor_tropes = {
            (wt.trope_id, wt.relevance_score, wt.justification)
            for wt in session.query(WorkTrope).filter_by(work_id=survivor.id).all()
        }
        assert survivor_tropes == {
            (survivor_trope_1.id, 1.0, "scout said so"),
            (survivor_trope_2.id, 1.0, "scout said so too"),
            (loser_only_trope.id, 0.75, "loser-only context"),
        }
        assert session.query(WorkTrope).filter_by(work_id=loser.id).all() == []

        assert session.query(DetectedDuplicate).count() == 0

        converged = db_.plan_works_merge(session)
        assert converged.summary() == {
            "works_same_isbn": 0,
            "works_same_identity": 0,
            "works_detected_duplicates": 0,
            "works_fuzzy_report_only": 0,
            "ignored_self_detections": 0,
        }


def test_drift_refusal_e2e_full_snapshot_equality(db_url):
    """A SEPARATE duplicate pair mints between the reviewed dry-run and apply — refuses (drift
    error carrying the delta + a fresh report path) and changes NOTHING: a full snapshot across
    works/editions/reading_history/suggestions/work_tropes/detected_duplicates is byte-identical
    before and after the refused apply (mirrors test_fallback_repair.py's drift test)."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn", deep_enriched_at=datetime(2026, 1, 1, tzinfo=UTC))
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        session.add(Edition(work_id=survivor.id, isbn_13="9780765311788", format="ebook"))
        session.add(Edition(work_id=loser.id, isbn_13="9780765311788", format="audiobook"))
        session.flush()

        clusters = db_.plan_works_merge(session)
        compositions = [db_.compose_cluster_merge(session, c) for c in db_.applyable_works_merge_clusters(clusters)]
        report_path = db_.write_works_merge_apply_report(clusters, compositions)

        # live traffic in the gap: a brand-new duplicate pair appears, unrelated to the reviewed
        # cluster.
        new_a = _work("Elantris", deep_enriched_at=datetime(2026, 1, 1, tzinfo=UTC))
        new_b = _work("Elantris")
        session.add_all([new_a, new_b])
        session.flush()
        session.add(DetectedDuplicate(work_id_a=new_a.id, work_id_b=new_b.id, source="deep_pass_redirect"))
        session.flush()

        def _snapshot():
            return {
                "works": {(w.id, w.title) for w in session.query(Work).all()},
                "editions": {(e.id, e.work_id, e.isbn_13, e.format) for e in session.query(Edition).all()},
                "reading_history": {
                    (rh.id, rh.edition_id, rh.user_id, rh.date_completed) for rh in session.query(ReadingHistory).all()
                },
                "suggestions": {(s.id, s.work_id, s.user_id, s.status) for s in session.query(Suggestions).all()},
                "work_tropes": {
                    (wt.work_id, wt.trope_id, wt.relevance_score, wt.justification)
                    for wt in session.query(WorkTrope).all()
                },
                "detected_duplicates": {(d.work_id_a, d.work_id_b) for d in session.query(DetectedDuplicate).all()},
            }

        before = _snapshot()

        with pytest.raises(db_.WorksMergeDriftError, match="drifted") as exc_info:
            db_.apply_works_merge(session, report_path)
        session.flush()

        assert any(
            t.startswith("delete_work:") and (str(new_a.id) in t or str(new_b.id) in t) for t in exc_info.value.delta
        )
        assert exc_info.value.fresh_report_path.exists()
        fresh_reviewable = db_.parse_works_merge_report(exc_info.value.fresh_report_path.read_text(encoding="utf-8"))
        assert exc_info.value.delta <= fresh_reviewable  # every delta token is present, re-reviewable

        after = _snapshot()
        assert after == before


def test_fuzzy_only_cluster_never_consumes_tokens_through_apply(db_url):
    """A fuzzy-only cluster (no shared ISBN, no shared identity, no detected_duplicates row —
    the ONLY edge is title similarity) appears in the plan/report but apply consumes ZERO tokens
    from it and never composes it — the structural exclusion (applyable_works_merge_clusters
    never includes fuzzy_report_only) holds all the way through the gate, both works survive."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        author_a = Author(name="Author A")
        author_b = Author(name="Author B")
        work_a = _work("Calling Bullshit Now")
        work_b = _work("Calling Bullshit")
        session.add_all([author_a, author_b, work_a, work_b])
        session.flush()
        # DIFFERENT authors (keeps this OUT of works_same_identity), no ISBN (OUT of
        # works_same_isbn), no detected_duplicates row -> the only edge left is fuzzy title
        # similarity.
        session.add(WorkContributor(work_id=work_a.id, author_id=author_a.id, role="Author"))
        session.add(WorkContributor(work_id=work_b.id, author_id=author_b.id, role="Author"))
        session.flush()

        clusters = db_.plan_works_merge(session)
        assert clusters.summary() == {
            "works_same_isbn": 0,
            "works_same_identity": 0,
            "works_detected_duplicates": 0,
            "works_fuzzy_report_only": 1,
            "ignored_self_detections": 0,
        }

        compositions = [db_.compose_cluster_merge(session, c) for c in db_.applyable_works_merge_clusters(clusters)]
        assert compositions == []  # fuzzy is structurally never composed

        report_path = db_.write_works_merge_apply_report(clusters, compositions)
        report_text = report_path.read_text(encoding="utf-8")
        assert "NEVER APPLIED" in report_text
        assert "Calling Bullshit" in report_text

        applied = db_.apply_works_merge(session, report_path)
        session.flush()

        assert applied["delete_work"] == 0
        assert session.get(Work, work_a.id) is not None
        assert session.get(Work, work_b.id) is not None


def test_identity_cluster_merges_through_apply_no_shared_isbn(db_url):
    """We-Are-Legion-shaped: punctuation-variant titles + shared author, no shared ISBN at all
    -> works_same_identity, merged through the real apply gate."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        author = Author(name="Dennis E. Taylor")
        w1 = _work("We Are Legion (We Are Bob)")
        w2 = _work("We are Legion; We are Bob", deep_enriched_at=datetime(2026, 6, 1, tzinfo=UTC))
        session.add_all([author, w1, w2])
        session.flush()
        session.add(WorkContributor(work_id=w1.id, author_id=author.id, role="Author"))
        session.add(WorkContributor(work_id=w2.id, author_id=author.id, role="Author"))
        session.flush()

        clusters = db_.plan_works_merge(session)
        assert clusters.summary()["works_same_identity"] == 1
        assert clusters.summary()["works_same_isbn"] == 0

        compositions = [db_.compose_cluster_merge(session, c) for c in db_.applyable_works_merge_clusters(clusters)]
        report_path = db_.write_works_merge_apply_report(clusters, compositions)

        applied = db_.apply_works_merge(session, report_path)
        session.flush()

        assert applied["delete_work"] == 1
        assert applied["drop_contributor"] == 1  # shared (author, role) pair, not duplicated
        assert applied["copy_contributor"] == 0
        assert session.query(Work).count() == 1
        survivor = session.query(Work).one()
        assert survivor.id == w2.id  # deep_enriched_at newest wins the survivor tiebreak
        assert session.query(WorkContributor).filter_by(work_id=survivor.id).count() == 1

        converged = db_.plan_works_merge(session)
        assert converged.summary()["works_same_identity"] == 0


def test_narrator_union_through_apply_repoint_and_drop(db_url):
    """Format-collision merge (both editions 'audiobook') where the loser edition carries a
    narrator absent on the survivor edition (repoint_narrator) AND a narrator already shared
    with the survivor (drop_narrator) — both paths execute and count through the real apply
    gate, not just compose_cluster_merge's plan."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("Mistborn", deep_enriched_at=datetime(2026, 1, 1, tzinfo=UTC))
        loser = _work("Mistborn")
        session.add_all([survivor, loser])
        session.flush()
        survivor_edition = Edition(work_id=survivor.id, isbn_13="9780765311788", format="audiobook")
        loser_edition = Edition(work_id=loser.id, isbn_13="9780765311788", format="audiobook")
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

        clusters = db_.plan_works_merge(session)
        compositions = [db_.compose_cluster_merge(session, c) for c in db_.applyable_works_merge_clusters(clusters)]
        report_path = db_.write_works_merge_apply_report(clusters, compositions)

        applied = db_.apply_works_merge(session, report_path)
        session.flush()

        assert applied["merge_edition"] == 1
        assert applied["repoint_narrator"] == 1
        assert applied["drop_narrator"] == 1
        assert session.get(Edition, loser_edition.id) is None

        remaining_narrators = {
            row.narrator_id
            for row in session.execute(
                select(edition_narrators.c.narrator_id).where(edition_narrators.c.edition_id == survivor_edition.id)
            ).all()
        }
        assert remaining_narrators == {shared_narrator.id, new_narrator.id}
