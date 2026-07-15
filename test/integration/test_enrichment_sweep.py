"""GH #97: plan_requeue surfaces works needing a (re)queued deep-enrichment pass."""

from datetime import UTC, datetime

import pytest

from agentic_librarian.db.models import DetectedDuplicate, Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl.enrichment_sweep import plan_requeue

pytestmark = pytest.mark.db_integration


def test_plan_requeue_classifies_three_works(db_url):
    manager = DatabaseManager(db_url)

    with manager.get_session() as s:
        # 1. Stamped + real trope -> excluded entirely.
        enriched = Work(title="Enriched Work", deep_enriched_at=datetime.now(UTC))
        s.add(enriched)
        s.flush()
        real_trope = Trope(name="Found Family")
        s.add(real_trope)
        s.flush()
        s.add(WorkTrope(work_id=enriched.id, trope_id=real_trope.id))

        # 2. Never deep-enriched -> "never_deep_enriched".
        unstamped = Work(title="Unstamped Work", deep_enriched_at=None)
        s.add(unstamped)
        s.flush()

        # 3. Stamped, but every linked trope is a fallback (== one of its own genres) ->
        # "no_real_trope".
        fallback_work = Work(title="Fallback-Only Work", deep_enriched_at=datetime.now(UTC), genres=["Fantasy"])
        s.add(fallback_work)
        s.flush()
        fallback_trope = Trope(name="Fantasy")
        s.add(fallback_trope)
        s.flush()
        s.add(WorkTrope(work_id=fallback_work.id, trope_id=fallback_trope.id))

        s.flush()
        enriched_id, unstamped_id, fallback_id = enriched.id, unstamped.id, fallback_work.id

    with manager.get_session() as s:
        plan = plan_requeue(s)

    by_id = {c.work_id: c for c in plan}
    assert enriched_id not in by_id
    assert by_id[unstamped_id].reason == "never_deep_enriched"
    assert by_id[unstamped_id].title == "Unstamped Work"
    assert by_id[fallback_id].reason == "no_real_trope"
    assert by_id[fallback_id].title == "Fallback-Only Work"


def test_plan_requeue_treats_zero_trope_links_as_no_real_trope(db_url):
    """A stamped work with zero trope links at all (e.g. the #123 warm-failure case, where
    every embedding was skipped) must also be flagged — no_real_trope isn't just
    fallback-vs-real, it's "no genuine trope at all"."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        work = Work(title="No Tropes At All", deep_enriched_at=datetime.now(UTC))
        s.add(work)
        s.flush()
        work_id = work.id

    with manager.get_session() as s:
        plan = plan_requeue(s)

    by_id = {c.work_id: c for c in plan}
    assert by_id[work_id].reason == "no_real_trope"


def test_plan_requeue_never_deep_enriched_wins_over_no_real_trope(db_url):
    """A work matching both conditions (unstamped AND fallback-only tropes) appears
    exactly once, classified as never_deep_enriched."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        work = Work(title="Both Conditions", deep_enriched_at=None, genres=["Fantasy"])
        s.add(work)
        s.flush()
        trope = Trope(name="Fantasy")
        s.add(trope)
        s.flush()
        s.add(WorkTrope(work_id=work.id, trope_id=trope.id))
        s.flush()
        work_id = work.id

    with manager.get_session() as s:
        plan = plan_requeue(s)

    matches = [c for c in plan if c.work_id == work_id]
    assert len(matches) == 1
    assert matches[0].reason == "never_deep_enriched"


@pytest.mark.parametrize(
    "side",
    [
        pytest.param("work_id_a", id="invoked-side"),
        pytest.param("work_id_b", id="twin-side"),
    ],
)
def test_plan_requeue_excludes_works_on_either_side_of_detected_duplicates(db_url, side):
    """GH #141: a work appearing on EITHER side of detected_duplicates needs the merge tool,
    not another paid deep pass — excluded from both enrichable classes and reported
    separately as "pending_merge", regardless of its own deep_enriched_at/trope state."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as s:
        # never_deep_enriched shape on BOTH rows — would normally be candidates.
        a = Work(title="Pending Merge A", deep_enriched_at=None)
        b = Work(title="Pending Merge B", deep_enriched_at=None)
        s.add_all([a, b])
        s.flush()
        s.add(DetectedDuplicate(work_id_a=a.id, work_id_b=b.id, source="deep_pass_redirect"))
        s.flush()
        a_id, b_id = a.id, b.id
        target_id = a_id if side == "work_id_a" else b_id

    with manager.get_session() as s:
        plan = plan_requeue(s)

    by_id = {c.work_id: c for c in plan}
    assert by_id[a_id].reason == "pending_merge"
    assert by_id[b_id].reason == "pending_merge"
    assert by_id[target_id].reason == "pending_merge"
    assert all(c.reason != "never_deep_enriched" for c in plan if c.work_id in (a_id, b_id))
    assert all(c.reason != "no_real_trope" for c in plan if c.work_id in (a_id, b_id))
