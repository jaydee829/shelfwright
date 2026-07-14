"""PR-D part 2 (GH #70): the gated fallback-pollution repair backfill, e2e-shaped per
CLAUDE.md rule 6 — the plan/report/apply round-trip is driven through the REAL functions
(plan_fallback_repair -> write_report -> parse_report -> apply_fallback_repair), mirroring
test/integration/test_dedup_backfill.py's apply-gate tests and test_fallback_prune.py's seeding
style. Embeddings are stubbed deterministically (monkeypatch fallback_repair.get_cached_embedding)
so cosine-distance classification is exact and reproducible without a live Gemini call."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from agentic_librarian.db.models import Trope, Work, WorkTrope
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.etl import fallback_repair as fr
from agentic_librarian.scouts import trope_manager as trope_manager_module

pytestmark = pytest.mark.db_integration

# Deterministic embedding space for these tests: "Dark"-derived vectors are cosine-IDENTICAL to
# "The Dark Night of the Soul"'s (attractor A); "Grim"-derived vectors are cosine-IDENTICAL to
# "Trial by Shadow"'s (attractor B) — TWO DISTINCT attractor clusters, each with its own trigger
# mood tag, so a nearest-trope lookup for one mood can never ambiguously tie against the OTHER
# attractor (two tropes at the exact same distance from a query vector is not how the real
# pollution incident looks — every attractor is its own semantic cluster; a same-distance tie
# would make "nearest" a Postgres row-order accident rather than a real classification).
# Everything else gets an orthogonal-ish vector so it never accidentally satisfies
# BOGUS_MATCH_THRESHOLD.
_ATTRACTOR_A_VEC = [1.0] + [0.0] * 1535
_ATTRACTOR_B_VEC = [0.0, 0.0, 1.0] + [0.0] * 1533
_OTHER_VEC = [0.0, 1.0] + [0.0] * 1534
_ATTRACTOR_VEC = _ATTRACTOR_A_VEC  # back-compat alias for the single-attractor tests below


def _fake_embed(model_name, text_):
    if text_ == "Dark":
        return _ATTRACTOR_A_VEC
    if text_ == "Grim":
        return _ATTRACTOR_B_VEC
    return _OTHER_VEC


@pytest.fixture(autouse=True)
def _stub_embeddings(monkeypatch):
    # plan_fallback_repair's bogus_targets scan calls get_cached_embedding via
    # etl/fallback_repair.py's own module-level binding; apply_fallback_repair's write_slug
    # phase separately constructs a REAL TropeManager (get_or_create_fallback_trope), which
    # calls get_cached_embedding via ITS OWN module-level binding
    # (scouts/trope_manager.py) — both import sites must be patched, since `from x import y`
    # binds a local name in each importing module's namespace rather than a shared reference.
    monkeypatch.setenv("GOOGLE_SEARCH_API_KEY", "dummy-key-for-construction")
    monkeypatch.setattr(fr, "get_cached_embedding", _fake_embed)
    monkeypatch.setattr(trope_manager_module, "get_cached_embedding", _fake_embed)


def _link(session, work, trope, *, justification=None):
    session.add(WorkTrope(work_id=work.id, trope_id=trope.id, justification=justification))


def test_full_round_trip_delete_write_slug_clear_stamp_prune(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        # work 1: polluted — mood "Dark" was semantically redirected onto the real trope "The
        # Dark Night of the Soul" (NULL-justified). No other trope on this work -> tropeless
        # after the delete -> gets exact-name slugs + a cleared deep_enriched_at stamp.
        work1 = Work(
            title="Polluted Work",
            genres=["Thriller"],
            moods=["Dark"],
            deep_enriched_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        # work 2: shares the SAME attractor trope, but via a real justified scout link — must
        # survive untouched.
        work2 = Work(title="Justified Work", genres=[], moods=[])
        # work 3 (Fix 1, deletion-triggered eligibility): a seeded TROPELESS work with its own
        # genres/moods AND a deep_enriched_at stamp — the #67-pruned-fast-pass shape. It has ZERO
        # work_tropes links, so plan_fallback_repair must plan NO delete_link, NO write_slug, and
        # NO clear_stamp for it: this run never touched it, so it must never touch it back.
        work3 = Work(
            title="Fast-Pass Tropeless Work",
            genres=["Thriller"],
            moods=["Dark"],
            deep_enriched_at=datetime(2026, 6, 1, tzinfo=UTC),
        )
        session.add_all([work1, work2, work3])
        session.flush()

        attractor = Trope(name="The Dark Night of the Soul", embedding=_ATTRACTOR_VEC)
        session.add(attractor)
        session.flush()

        _link(session, work1, attractor, justification=None)  # bogus: NULL-justified + derivable
        _link(session, work2, attractor, justification="scout: framed as a spiritual crisis arc")
        # work3 gets NO links at all — see above.
        session.flush()

        # --- dry-run plan ---
        plan = fr.plan_fallback_repair(session)
        assert len(plan.delete_links) == 1
        assert plan.delete_links[0].work_id == work1.id
        assert plan.delete_links[0].trope_id == attractor.id
        assert {s.trope_name for s in plan.write_slugs if s.work_id == work1.id} == {"Thriller", "Dark"}
        assert [c.work_id for c in plan.clear_stamps] == [work1.id]
        assert [p.trope_id for p in plan.prune_tropes] == []  # attractor still has work2's link

        # work3: the plan must be entirely silent about it — no delete_link (nothing to delete),
        # no write_slug (never eligible — zero delete_links planned for it), no clear_stamp
        # (same eligibility gate; its stamp is left alone).
        assert all(d.work_id != work3.id for d in plan.delete_links)
        assert all(w.work_id != work3.id for w in plan.write_slugs)
        assert all(c.work_id != work3.id for c in plan.clear_stamps)

        report_path = fr.write_report(plan)
        reviewed_tokens = fr.parse_report(report_path.read_text(encoding="utf-8"))

        # --- apply ---
        applied = fr.apply_fallback_repair(session, report_path)
        session.flush()

        assert applied["delete_links"] == 1
        assert applied["write_slugs"] == 2
        assert applied["clear_stamps"] == 1
        assert applied["prune_tropes"] == 0  # work2's justified link keeps the attractor alive

        # work1: attractor link gone, exact-name slugs present, stamp cleared
        work1_links = {
            session.get(Trope, wt.trope_id).name for wt in session.query(WorkTrope).filter_by(work_id=work1.id).all()
        }
        assert work1_links == {"Thriller", "Dark"}
        assert session.get(Work, work1.id).deep_enriched_at is None

        # work2: untouched
        work2_links = session.query(WorkTrope).filter_by(work_id=work2.id).all()
        assert len(work2_links) == 1
        assert work2_links[0].trope_id == attractor.id
        assert work2_links[0].justification is not None

        # work3: STILL zero links, stamp UNTOUCHED — apply must not have re-added fallback slugs
        # or cleared its stamp, even though it superficially "has no real trope" too.
        work3_links = session.query(WorkTrope).filter_by(work_id=work3.id).all()
        assert work3_links == []
        assert session.get(Work, work3.id).deep_enriched_at == datetime(2026, 6, 1, tzinfo=UTC)

        # --- convergence: re-plan finds nothing left to do ---
        converged = fr.plan_fallback_repair(session)
        assert converged.summary() == {"delete_links": 0, "write_slugs": 0, "clear_stamps": 0, "prune_tropes": 0}

        # sanity: the report's own tokens matched what got applied (round-trip already proven
        # elsewhere; this just confirms the fixture wiring produced a non-empty reviewed set)
        assert reviewed_tokens["delete_links"]


def test_drift_gate_refuses_and_changes_nothing(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        work1 = Work(title="Drift Work 1", genres=[], moods=["Dark"])
        session.add(work1)
        session.flush()

        attractor = Trope(name="The Dark Night of the Soul", embedding=_ATTRACTOR_VEC)
        session.add(attractor)
        session.flush()
        _link(session, work1, attractor, justification=None)
        session.flush()

        reviewed_plan = fr.plan_fallback_repair(session)
        report_path = fr.write_report(reviewed_plan)

        # live traffic in the gap: a NEW bogus NULL-justified link appears on a second work,
        # sharing the same attractor.
        work2 = Work(title="Drift Work 2", genres=[], moods=["Dark"])
        session.add(work2)
        session.flush()
        _link(session, work2, attractor, justification=None)
        session.flush()

        # snapshot state before the refused apply, to assert NOTHING changed
        work_tropes_before = {(wt.work_id, wt.trope_id, wt.justification) for wt in session.query(WorkTrope).all()}
        works_stamps_before = {w.id: w.deep_enriched_at for w in session.query(Work).all()}
        tropes_before = {t.id for t in session.query(Trope).all()}

        with pytest.raises(fr.FallbackRepairDriftError, match="drifted") as exc_info:
            fr.apply_fallback_repair(session, report_path)
        session.flush()

        # Minor 5: the refusal carries the offending delta TOKENS (not just counts) and a fresh
        # report path written from the drifted plan, ready for immediate re-review.
        new_delete_token = f"delete_link:{(work2.id, attractor.id)}"
        assert new_delete_token in exc_info.value.delta["delete_links"]
        assert exc_info.value.fresh_report_path.exists()
        fresh_reviewable = fr.parse_report(exc_info.value.fresh_report_path.read_text(encoding="utf-8"))
        assert new_delete_token in fresh_reviewable["delete_links"]

        work_tropes_after = {(wt.work_id, wt.trope_id, wt.justification) for wt in session.query(WorkTrope).all()}
        works_stamps_after = {w.id: w.deep_enriched_at for w in session.query(Work).all()}
        tropes_after = {t.id for t in session.query(Trope).all()}

        assert work_tropes_after == work_tropes_before
        assert works_stamps_after == works_stamps_before
        assert tropes_after == tropes_before


def test_skipped_stale_reports_shrinkage_when_fresh_is_subset_of_reviewed(db_url):
    """Minor 4: fresh ⊂ reviewed (some reviewed rows were already applied/vanished by the time
    of this apply) is NOT a drift refusal — it applies cleanly, and skipped_stale reports the
    exact shrinkage: the reviewed tokens no longer present in the fresh plan."""
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        work1 = Work(title="Shrinkage Work 1", genres=[], moods=["Dark"])
        work2 = Work(title="Shrinkage Work 2", genres=[], moods=["Dark"])
        session.add_all([work1, work2])
        session.flush()

        attractor = Trope(name="The Dark Night of the Soul", embedding=_ATTRACTOR_VEC)
        session.add(attractor)
        session.flush()
        _link(session, work1, attractor, justification=None)
        _link(session, work2, attractor, justification=None)
        session.flush()

        # reviewed report sees BOTH bogus links AND (Fix 1: each work has >=1 delete_link, so
        # both are write_slug-eligible) the exact-name "Dark" slug restore for each work.
        reviewed_plan = fr.plan_fallback_repair(session)
        assert len(reviewed_plan.delete_links) == 2
        assert len(reviewed_plan.write_slugs) == 2
        report_path = fr.write_report(reviewed_plan)

        # work2's bogus link is removed out-of-band before apply runs (simulating "already
        # applied by someone else" / "the row is simply gone by apply time") — the fresh re-plan
        # sees only work1's delete_link (a SUBSET of what was reviewed): work2 now has ZERO
        # links, so Fix 1's eligibility gate also drops its reviewed write_slug token — TWO
        # reviewed tokens (delete_link + write_slug) vanish for the one out-of-band change.
        stale_link = session.query(WorkTrope).filter_by(work_id=work2.id, trope_id=attractor.id).one()
        session.delete(stale_link)
        session.flush()

        applied = fr.apply_fallback_repair(session, report_path)
        session.flush()

        assert applied["delete_links"] == 1  # only work1's link actually got deleted
        assert applied["write_slugs"] == 1  # only work1's "Dark" slug actually got written
        # skipped_stale must account for BOTH vanished work2 tokens (delete_link + write_slug) —
        # the shrinkage Minor 4 asks to be reported, not silently dropped.
        assert applied["skipped_stale"] == 2


def test_prune_trope_deleted_when_all_links_bogus_kept_when_justified_link_survives(db_url):
    manager = DatabaseManager(db_url)
    with manager.get_session() as session:
        session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

        # attractor A: only ever bogus-linked -> pruned after apply
        attractor_a = Trope(name="The Dark Night of the Soul", embedding=_ATTRACTOR_A_VEC)
        # attractor B: bogus-linked on one work, justified-linked on another -> survives. Its own
        # distinct trigger tag ("Grim") keeps it from tying against attractor A's nearest-lookup.
        attractor_b = Trope(name="Trial by Shadow", embedding=_ATTRACTOR_B_VEC)
        session.add_all([attractor_a, attractor_b])
        session.flush()

        work_a = Work(title="Prune Target Work", genres=[], moods=["Dark"])
        work_b1 = Work(title="Kept Attractor Work 1", genres=[], moods=["Grim"])
        work_b2 = Work(title="Kept Attractor Work 2", genres=[], moods=[])
        session.add_all([work_a, work_b1, work_b2])
        session.flush()

        _link(session, work_a, attractor_a, justification=None)
        _link(session, work_b1, attractor_b, justification=None)
        _link(session, work_b2, attractor_b, justification="scout: real narrative arc")
        session.flush()

        plan = fr.plan_fallback_repair(session)
        report_path = fr.write_report(plan)

        applied = fr.apply_fallback_repair(session, report_path)
        session.flush()

        assert applied["prune_tropes"] == 1
        assert session.get(Trope, attractor_a.id) is None  # pruned: zero links remained
        assert session.get(Trope, attractor_b.id) is not None  # kept: work_b2's justified link
        remaining_b_links = session.query(WorkTrope).filter_by(trope_id=attractor_b.id).all()
        assert len(remaining_b_links) == 1
        assert remaining_b_links[0].work_id == work_b2.id
