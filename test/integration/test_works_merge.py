"""db_integration tests for plan_works_merge (PR-2 part 1, Spec 2026-07-14): the four
detection classes exercised against a real Postgres session, seeded with the actual prod-shaped
duplicate scenarios named in the design spec's "Verified findings" — the Beware of Chicken
pair (same ISBN, one dirty comma-joined author), the We Are Legion pair (punctuation-variant
titles, no shared ISBN), the Beware of Chicken 2 sequel non-pair, and a detected_duplicates row
(#141/#143 feed).

READ ONLY — plan_works_merge never mutates. No apply step exists yet (H2)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from agentic_librarian.db.models import (
    Author,
    DetectedDuplicate,
    Edition,
    Work,
    WorkContributor,
    WorkTrope,
)
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import dedup_backfill as db_

pytestmark = pytest.mark.db_integration


def _work(title: str, *, deep_enriched_at=None) -> Work:
    return Work(title=title, deep_enriched_at=deep_enriched_at)


def test_empty_db_plan_is_empty(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        clusters = db_.plan_works_merge(session)
        assert clusters.summary() == {
            "works_same_isbn": 0,
            "works_same_isbn_title_mismatch": 0,
            "works_same_identity": 0,
            "works_detected_duplicates": 0,
            "works_fuzzy_report_only": 0,
            "ignored_self_detections": 0,
        }


def test_beware_of_chicken_pair_same_isbn_dirty_author(db_url):
    """Real prod scenario: 9e9cfc45 (slug-only, malformed comma-joined author, no tropes) /
    a5e56605 (15 justified tropes, clean author) — same ISBN 9781039452275. Highest-confidence
    class (works_same_isbn) must catch it even though the author strings differ, and the
    survivor must be the one with justified trope links."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        dirty_author = Author(name="Casualfarmer, CasualFarmer")
        clean_author = Author(name="CasualFarmer")
        w_dirty = _work("Beware of Chicken")
        w_clean = _work("Beware of Chicken", deep_enriched_at=datetime(2026, 6, 12, tzinfo=UTC))
        session.add_all([dirty_author, clean_author, w_dirty, w_clean])
        session.flush()

        session.add(WorkContributor(work_id=w_dirty.id, author_id=dirty_author.id, role="Author"))
        session.add(WorkContributor(work_id=w_clean.id, author_id=clean_author.id, role="Author"))
        session.add(Edition(work_id=w_dirty.id, isbn_13="9781039452275", format="ebook"))
        session.add(Edition(work_id=w_clean.id, isbn_13="9781039452275", format="audiobook"))
        session.flush()

        # 15 justified tropes on the clean work (real scout output); none on the dirty one.
        trope_id = uuid4()
        from agentic_librarian.db.models import Trope

        session.add(Trope(id=trope_id, name="Found Family"))
        session.flush()
        session.add(WorkTrope(work_id=w_clean.id, trope_id=trope_id, justification="scout said so"))
        session.flush()

        clusters = db_.plan_works_merge(session)
        assert clusters.summary()["works_same_isbn"] == 1
        cluster = clusters.same_isbn[0]
        assert set(cluster.work_ids) == {w_dirty.id, w_clean.id}
        assert cluster.survivor_id == w_clean.id  # most justified trope links wins

        # Never double-counted into a weaker class.
        assert clusters.summary()["works_same_identity"] == 0
        assert clusters.summary()["works_fuzzy_report_only"] == 0


def test_we_are_legion_pair_punctuation_variant_no_shared_isbn(db_url):
    """Real prod scenario: 14c8f3b5/7fc21c9e — punctuation-variant titles, same author, no
    shared ISBN. Must land in works_same_identity (fold() equal + author overlap), not
    works_same_isbn (no edition/ISBN data at all here)."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        author = Author(name="Dennis E. Taylor")
        w1 = _work("We Are Legion (We Are Bob)")
        w2 = _work("We are Legion; We are Bob")
        session.add_all([author, w1, w2])
        session.flush()
        session.add(WorkContributor(work_id=w1.id, author_id=author.id, role="Author"))
        session.add(WorkContributor(work_id=w2.id, author_id=author.id, role="Author"))
        session.flush()

        clusters = db_.plan_works_merge(session)
        assert clusters.summary()["works_same_identity"] == 1
        cluster = clusters.same_identity[0]
        assert set(cluster.work_ids) == {w1.id, w2.id}
        assert clusters.summary()["works_same_isbn"] == 0


def test_beware_of_chicken_2_sequel_is_never_a_pair(db_url):
    """Real prod caveat from the spec: 'Beware of Chicken 2' is the SEQUEL to 'Beware of
    Chicken', not a duplicate — the series guard must block it from every class, including
    fuzzy (a sequel is highly title-similar by construction)."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        author = Author(name="CasualFarmer")
        w1 = _work("Beware of Chicken")
        w2 = _work("Beware of Chicken 2")
        session.add_all([author, w1, w2])
        session.flush()
        session.add(WorkContributor(work_id=w1.id, author_id=author.id, role="Author"))
        session.add(WorkContributor(work_id=w2.id, author_id=author.id, role="Author"))
        session.flush()

        clusters = db_.plan_works_merge(session)
        assert clusters.summary() == {
            "works_same_isbn": 0,
            "works_same_isbn_title_mismatch": 0,
            "works_same_identity": 0,
            "works_detected_duplicates": 0,
            "works_fuzzy_report_only": 0,
            "ignored_self_detections": 0,
        }


def test_beware_shaped_triple_plan_applyable_pair_plus_mismatch_sequel(db_url):
    """H3 hardening (2026-07-15, real prod dry-run): TWO 'Beware of Chicken' works sharing an
    ISBN (a genuine duplicate pair) PLUS 'Beware of Chicken 2' sharing THE SAME ISBN (the
    sequel carrying its predecessor's ISBN — real prod pollution). plan_works_merge must
    produce ONE applyable same_isbn cluster (just the two equal-fold works) and mismatch report
    entries for the sequel — the sequel must never enter an applyable cluster."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        author = Author(name="CasualFarmer")
        boc_dirty = _work("Beware of Chicken")
        boc_clean = _work("Beware of Chicken", deep_enriched_at=datetime(2026, 6, 12, tzinfo=UTC))
        boc_2 = _work("Beware of Chicken 2")
        session.add_all([author, boc_dirty, boc_clean, boc_2])
        session.flush()
        session.add(WorkContributor(work_id=boc_dirty.id, author_id=author.id, role="Author"))
        session.add(WorkContributor(work_id=boc_clean.id, author_id=author.id, role="Author"))
        session.add(WorkContributor(work_id=boc_2.id, author_id=author.id, role="Author"))
        session.add(Edition(work_id=boc_dirty.id, isbn_13="9781039452275", format="ebook"))
        session.add(Edition(work_id=boc_clean.id, isbn_13="9781039452275", format="audiobook"))
        session.add(Edition(work_id=boc_2.id, isbn_13="9781039452275", format="ebook"))
        session.flush()

        clusters = db_.plan_works_merge(session)

        assert clusters.summary()["works_same_isbn"] == 1
        assert set(clusters.same_isbn[0].work_ids) == {boc_dirty.id, boc_clean.id}

        mismatch_ids = {wid for c in clusters.same_isbn_title_mismatch for wid in c.work_ids}
        assert boc_2.id in mismatch_ids

        applyable_ids = {wid for c in db_.applyable_works_merge_clusters(clusters) for wid in c.work_ids}
        assert boc_2.id not in applyable_ids
        assert applyable_ids == {boc_dirty.id, boc_clean.id}


def test_ender_shaped_chain_plan_zero_applyable_clusters(db_url):
    """The prod dry-run's actual false-merge shape: several DISTINCT books sharing ONE bogus
    ISBN (the real report chained 14 unrelated novels this way). plan_works_merge must produce
    ZERO applyable same_isbn clusters — only a report-only mismatch cluster, visible for
    operator triage."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        titles = ["Ender's Game", "Ender's Shadow", "Shadow of the Hegemon", "The Shadow Cabinet"]
        works = [_work(t) for t in titles]
        session.add_all(works)
        session.flush()
        for w in works:
            session.add(Edition(work_id=w.id, isbn_13="9780000000000", format="ebook"))
        session.flush()

        clusters = db_.plan_works_merge(session)

        assert clusters.same_isbn == []
        assert clusters.summary()["works_same_isbn"] == 0
        mismatch_ids = {wid for c in clusters.same_isbn_title_mismatch for wid in c.work_ids}
        assert mismatch_ids == {w.id for w in works}
        assert db_.applyable_works_merge_clusters(clusters) == []


def test_detected_duplicates_row_lands_in_its_own_class_with_correct_survivor(db_url):
    """A #141/#143 detected_duplicates row (work_id_a = the invoked/dirty work, work_id_b =
    the resolved twin) must appear under works_detected_duplicates with the more-enriched work
    picked as survivor — independent of ISBN/title data (these two works have unrelated
    titles, proving this class does not depend on the other three)."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        invoked = _work("Calling Bullshit")
        twin = _work("Calling Bullshit: The Art of Skepticism", deep_enriched_at=datetime(2026, 7, 1, tzinfo=UTC))
        session.add_all([invoked, twin])
        session.flush()
        session.add(DetectedDuplicate(work_id_a=invoked.id, work_id_b=twin.id, source="deep_pass_redirect"))
        session.flush()

        clusters = db_.plan_works_merge(session)
        assert clusters.summary()["works_detected_duplicates"] == 1
        cluster = clusters.detected_duplicates[0]
        assert set(cluster.work_ids) == {invoked.id, twin.id}
        assert cluster.survivor_id == twin.id


def test_detected_duplicates_unordered_pair_both_rows_collapse_to_one_cluster(db_url):
    """PR-2's spec item 6 note: both (A, B) and (B, A) rows can exist for the same cluster
    (the composite PK is (work_id_a, work_id_b), not order-normalized) — detection must
    de-duplicate by the unordered pair, not assume a single canonical row."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        a = _work("Yellowface")
        b = _work("Yellow Face")
        session.add_all([a, b])
        session.flush()
        session.add(DetectedDuplicate(work_id_a=a.id, work_id_b=b.id, source="deep_pass_redirect"))
        session.add(DetectedDuplicate(work_id_a=b.id, work_id_b=a.id, source="deep_pass_redirect"))
        session.flush()

        clusters = db_.plan_works_merge(session)
        assert clusters.summary()["works_detected_duplicates"] == 1


def test_self_referential_detected_duplicate_row_ignored_not_crashed(db_url):
    """H2 fix 1: a bad feed row where work_id_a == work_id_b (a work 'duplicate' of itself) must
    not crash the planner — `frozenset((A, A))` used to break plan_works_merge_clusters' union-
    find unpack (`a, b = tuple(pair)` on a size-1 frozenset). It is skipped at feed ingestion and
    counted under ignored_self_detections, visible in the plan summary rather than silent."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        w = _work("Self-Referential Work")
        session.add(w)
        session.flush()
        session.add(DetectedDuplicate(work_id_a=w.id, work_id_b=w.id, source="deep_pass_redirect"))
        session.flush()

        clusters = db_.plan_works_merge(session)  # must not raise

        assert clusters.summary()["ignored_self_detections"] == 1
        assert clusters.summary()["works_detected_duplicates"] == 0
        assert clusters.detected_duplicates == []


def test_fuzzy_class_never_contains_pairs_from_stronger_classes(db_url):
    """A same-ISBN pair plus an unrelated, merely fuzzy-similar pair: the fuzzy class must
    contain only the pair not already caught by a stronger class."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        # Strong pair (same ISBN).
        w1 = _work("Mistborn")
        w2 = _work("Mistborn")
        session.add_all([w1, w2])
        session.flush()
        session.add(Edition(work_id=w1.id, isbn_13="9780765350381", format="ebook"))
        session.add(Edition(work_id=w2.id, isbn_13="9780765350381", format="audiobook"))

        # Fuzzy-only pair: similar-but-not-identical folded titles, no ISBN/author overlap.
        w3 = _work("The Way of Kings Prime")
        w4 = _work("The Way of Kings")
        session.add_all([w3, w4])
        session.flush()

        clusters = db_.plan_works_merge(session)
        assert clusters.summary()["works_same_isbn"] == 1
        fuzzy_ids = {wid for c in clusters.fuzzy_report_only for wid in c.work_ids}
        same_isbn_ids = {wid for c in clusters.same_isbn for wid in c.work_ids}
        assert fuzzy_ids.isdisjoint(same_isbn_ids)
        assert {w3.id, w4.id} <= fuzzy_ids


def test_plan_works_merge_never_mutates(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        author = Author(name="Dennis E. Taylor")
        w1 = _work("We Are Legion (We Are Bob)")
        w2 = _work("We are Legion; We are Bob")
        session.add_all([author, w1, w2])
        session.flush()
        session.add(WorkContributor(work_id=w1.id, author_id=author.id, role="Author"))
        session.add(WorkContributor(work_id=w2.id, author_id=author.id, role="Author"))
        session.flush()

        db_.plan_works_merge(session)
        session.flush()
        assert session.query(Work).count() == 2


def test_render_works_merge_report_never_applied_marker_present(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        w1 = _work("The Way of Kings Prime")
        w2 = _work("The Way of Kings")
        session.add_all([w1, w2])
        session.flush()

        clusters = db_.plan_works_merge(session)
        report = db_.render_works_merge_report(clusters, db_target="localhost/test")
        assert "works_fuzzy_report_only" in report
        assert "NEVER APPLIED" in report
        assert "localhost/test" in report


# --------------------------------------------------------------------------------------------
# --promote-pair (H4): db_integration coverage for the CLI helper function
# dedup_backfill.promote_detected_duplicate_pair — driven directly (not through the CLI/argparse
# layer, which is covered by test/unit/test_clean_catalog_promote_pair_cli.py with the seam
# mocked). Two titles chosen deliberately unrelated (no shared ISBN, no author overlap, no
# fold-equal/fuzzy title match) so the ONLY way this pair could ever land in a plan is via the
# operator promotion itself — proving the feed integration, not some other class accidentally
# catching the same pair.
# --------------------------------------------------------------------------------------------


def test_promote_pair_lands_as_applyable_detected_duplicates_cluster(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        w1 = _work("Calling Bullshit")
        w2 = _work("The Tao of Pooh", deep_enriched_at=datetime(2026, 7, 1, tzinfo=UTC))
        session.add_all([w1, w2])
        session.flush()

        # Sanity: unrelated titles never cluster on their own.
        assert db_.plan_works_merge(session).summary()["works_detected_duplicates"] == 0

        result = db_.promote_detected_duplicate_pair(session, w1.id, w2.id)
        assert result.already_existed is False
        assert result.title_a == "Calling Bullshit"
        assert result.title_b == "The Tao of Pooh"

        clusters = db_.plan_works_merge(session)
        assert clusters.summary()["works_detected_duplicates"] == 1
        cluster = clusters.detected_duplicates[0]
        assert set(cluster.work_ids) == {w1.id, w2.id}
        assert cluster.survivor_id == w2.id  # more-enriched work wins

        applyable_ids = {wid for c in db_.applyable_works_merge_clusters(clusters) for wid in c.work_ids}
        assert applyable_ids == {w1.id, w2.id}


def test_promote_pair_rerun_is_idempotent_row_count_unchanged(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        w1 = _work("Calling Bullshit")
        w2 = _work("The Tao of Pooh")
        session.add_all([w1, w2])
        session.flush()

        first = db_.promote_detected_duplicate_pair(session, w1.id, w2.id)
        second = db_.promote_detected_duplicate_pair(session, w1.id, w2.id)

        assert first.already_existed is False
        assert second.already_existed is True
        assert session.query(DetectedDuplicate).count() == 1


def test_promote_pair_then_flows_through_apply_works_merge_e2e(db_url):
    """Promoted pair -> plan -> compose -> report -> apply_works_merge: the merge succeeds (one
    work survives) and the promoted detection row is consumed (deleted) by the apply, same as any
    other detected_duplicates-class cluster."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        survivor = _work("The Tao of Pooh", deep_enriched_at=datetime(2026, 7, 1, tzinfo=UTC))
        loser = _work("Calling Bullshit")
        session.add_all([survivor, loser])
        session.flush()

        db_.promote_detected_duplicate_pair(session, loser.id, survivor.id)

        clusters = db_.plan_works_merge(session)
        assert clusters.summary()["works_detected_duplicates"] == 1
        compositions = [db_.compose_cluster_merge(session, c) for c in db_.applyable_works_merge_clusters(clusters)]
        report_path = db_.write_works_merge_apply_report(clusters, compositions)

        applied = db_.apply_works_merge(session, report_path)
        session.flush()

        assert applied["delete_work"] == 1
        assert applied["delete_detection"] == 1
        assert session.get(Work, survivor.id) is not None
        assert session.get(Work, loser.id) is None
        assert session.query(DetectedDuplicate).count() == 0

        # A fresh re-plan converges to zero clusters.
        assert db_.plan_works_merge(session).summary()["works_detected_duplicates"] == 0
