"""Pure-logic unit tests for etl/fallback_repair.py (PR-D part 2, GH #70): the classification
core (_plan_from_data), op-tagged token round-trip, fail-closed report parsing, and the
apply-gate's drift-refusal contract — all exercised WITHOUT a live Postgres session (that's the
db_integration suite's job, test/integration/test_fallback_repair.py)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from agentic_librarian.etl.fallback_repair import (
    ClearStamp,
    DeleteLink,
    FallbackRepairPlan,
    PruneTrope,
    WriteSlug,
    _plan_from_data,
    _WorkData,
    parse_report,
    plan_delta,
    plan_tokens,
    write_report,
)

# --------------------------------------------------------------------------------------------
# Classification: bogus vs legitimate (_plan_from_data)
# --------------------------------------------------------------------------------------------


def _work(links, genres=None, moods=None, deep_enriched_at=None, work_id=None):
    return _WorkData(
        work_id=work_id or uuid4(),
        genres=genres or [],
        moods=moods or [],
        deep_enriched_at=deep_enriched_at,
        links=links,
    )


@pytest.mark.parametrize(
    "case_name, justification, is_bogus_target, expect_delete",
    [
        ("null_justified_bogus_target_deleted", None, True, True),
        ("justified_link_never_touched_even_if_bogus_target", "scout said so", True, False),
        ("null_justified_not_a_bogus_target_never_touched", None, False, False),
    ],
)
def test_delete_link_classification(case_name, justification, is_bogus_target, expect_delete):
    """The distinguisher: NULL justification AND membership in bogus_targets(work) are BOTH
    required. A justified link is never touched even when it happens to coincide with a
    recomputed semantic-fallback target; a NULL-justified link whose trope is NOT derivable
    from this work's tags (real scout NULL-justification, e.g. semantic-collapse attractors) is
    never touched either."""
    trope_id = uuid4()
    work = _work(
        links=[(trope_id, "The Dark Night of the Soul", justification)],
        genres=["Fantasy"],
        moods=["Dark"],
    )
    targets = {trope_id} if is_bogus_target else set()

    plan = _plan_from_data([work], {trope_id: "The Dark Night of the Soul"}, {work.work_id: targets})

    deleted_trope_ids = {d.trope_id for d in plan.delete_links}
    assert (trope_id in deleted_trope_ids) is expect_delete


def test_exact_name_slug_never_a_bogus_target_by_construction():
    """bogus_targets (the session-touching half, tested in the db_integration suite) never adds
    an exact-name-match trope to its result — this is enforced structurally by
    _nearest_trope_by_name returning None on an exact match before any embedding/distance
    lookup happens. Here we assert the classification core's contract: if bogus_targets_by_work
    reports NO targets for a work (the exact-name-match outcome), no delete is planned even for
    a NULL-justified link."""
    trope_id = uuid4()
    work = _work(links=[(trope_id, "Fantasy", None)], genres=["Fantasy"], moods=[])

    plan = _plan_from_data([work], {trope_id: "Fantasy"}, {work.work_id: set()})

    assert plan.delete_links == []


class TestDeletionTriggeredEligibility:
    """Fix 1 (adjudicated design change, review pass 2): write_slug/clear_stamp are eligible
    ONLY for a work that has >=1 delete_link planned. Without this gate a zero-link work or an
    untouched legitimately-stamped work would also qualify by the old 'no real trope survives'
    test alone."""

    def test_zero_link_work_gets_no_write_slug_or_clear_stamp(self):
        """A work with ZERO pre-existing trope links (never touched by the old fallback writer)
        must not get write_slug/clear_stamp planned — that would re-add fallback tropes the #67
        prune deliberately removed from fast-pass works."""
        work = _work(
            links=[],
            genres=["Thriller"],
            moods=["Dark"],
            deep_enriched_at="2026-01-01T00:00:00Z",
        )
        plan = _plan_from_data([work], {}, {work.work_id: set()})

        assert plan.write_slugs == []
        assert plan.clear_stamps == []

    def test_linkless_and_stamped_work_untouched(self):
        """A work with zero links but a deep_enriched_at stamp (a legitimately-stamped
        confirmed-empty work the #97 sweep already knows about) must be left entirely alone —
        no write_slug, no clear_stamp, no delete_link. Clearing its stamp would erase the
        sweep's repeat-cost signal for a work this run never touched."""
        work = _work(
            links=[],
            genres=[],
            moods=[],
            deep_enriched_at="2026-01-01T00:00:00Z",
        )
        plan = _plan_from_data([work], {}, {work.work_id: set()})

        assert plan.delete_links == []
        assert plan.write_slugs == []
        assert plan.clear_stamps == []

    def test_work_with_only_justified_links_and_no_bogus_targets_untouched(self):
        """A work with real (justified) links and no bogus targets plans no delete_link, so it
        must not become eligible for write_slug/clear_stamp either, even though has_real_remaining
        would still be True here anyway — this asserts the eligibility gate specifically, not
        just the pre-existing has_real_remaining behavior."""
        real_trope_id = uuid4()
        work = _work(
            links=[(real_trope_id, "Found Family", "scout")],
            genres=["Thriller"],
            moods=[],
            deep_enriched_at="2026-01-01T00:00:00Z",
        )
        plan = _plan_from_data([work], {real_trope_id: "Found Family"}, {work.work_id: set()})

        assert plan.delete_links == []
        assert plan.write_slugs == []
        assert plan.clear_stamps == []


class TestWriteSlugAndClearStamp:
    def test_write_slug_planned_when_no_real_trope_remains(self):
        bogus_trope_id = uuid4()
        work = _work(
            links=[(bogus_trope_id, "The Dark Night of the Soul", None)],
            genres=["Thriller"],
            moods=["Dark"],
        )
        plan = _plan_from_data([work], {bogus_trope_id: "The Dark Night of the Soul"}, {work.work_id: {bogus_trope_id}})

        slug_names = {s.trope_name for s in plan.write_slugs if s.work_id == work.work_id}
        assert slug_names == {"Thriller", "Dark"}

    def test_no_write_slug_when_real_trope_survives(self):
        real_trope_id = uuid4()
        work = _work(
            links=[(real_trope_id, "Found Family", "scout justification")],
            genres=["Thriller"],
            moods=["Dark"],
        )
        plan = _plan_from_data([work], {real_trope_id: "Found Family"}, {work.work_id: set()})

        assert plan.write_slugs == []

    def test_no_duplicate_slug_for_already_linked_exact_name(self):
        """A work that already carries an exact-name 'Thriller' slug link (untouched, real=False
        but not planned for deletion) must not get a second write_slug for the same name."""
        bogus_trope_id = uuid4()
        existing_slug_id = uuid4()
        work = _work(
            links=[
                (bogus_trope_id, "The Dark Night of the Soul", None),
                (existing_slug_id, "Thriller", None),  # exact-name slug already present
            ],
            genres=["Thriller"],
            moods=["Dark"],
        )
        plan = _plan_from_data(
            [work],
            {bogus_trope_id: "The Dark Night of the Soul", existing_slug_id: "Thriller"},
            {work.work_id: {bogus_trope_id}},
        )

        slug_names = {s.trope_name for s in plan.write_slugs}
        assert slug_names == {"Dark"}

    def test_clear_stamp_planned_when_no_real_trope_and_stamped(self):
        bogus_trope_id = uuid4()
        work = _work(
            links=[(bogus_trope_id, "The Dark Night of the Soul", None)],
            genres=[],
            moods=["Dark"],
            deep_enriched_at="2026-01-01T00:00:00Z",
        )
        plan = _plan_from_data([work], {bogus_trope_id: "The Dark Night of the Soul"}, {work.work_id: {bogus_trope_id}})

        assert [c.work_id for c in plan.clear_stamps] == [work.work_id]

    def test_no_clear_stamp_when_not_stamped(self):
        bogus_trope_id = uuid4()
        work = _work(
            links=[(bogus_trope_id, "The Dark Night of the Soul", None)],
            genres=[],
            moods=["Dark"],
            deep_enriched_at=None,
        )
        plan = _plan_from_data([work], {bogus_trope_id: "The Dark Night of the Soul"}, {work.work_id: {bogus_trope_id}})

        assert plan.clear_stamps == []

    def test_no_clear_stamp_when_real_trope_survives(self):
        real_trope_id = uuid4()
        work = _work(
            links=[(real_trope_id, "Found Family", "scout")],
            genres=[],
            moods=[],
            deep_enriched_at="2026-01-01T00:00:00Z",
        )
        plan = _plan_from_data([work], {real_trope_id: "Found Family"}, {work.work_id: set()})

        assert plan.clear_stamps == []


class TestPruneTrope:
    def test_pruned_when_all_links_deleted(self):
        trope_id = uuid4()
        w1 = _work(links=[(trope_id, "The Dark Night of the Soul", None)], genres=[], moods=["Dark"])
        w2 = _work(links=[(trope_id, "The Dark Night of the Soul", None)], genres=[], moods=["Dark"])
        plan = _plan_from_data(
            [w1, w2],
            {trope_id: "The Dark Night of the Soul"},
            {w1.work_id: {trope_id}, w2.work_id: {trope_id}},
        )

        assert [p.trope_id for p in plan.prune_tropes] == [trope_id]

    def test_not_pruned_when_a_justified_link_survives(self):
        trope_id = uuid4()
        w1 = _work(links=[(trope_id, "The Dark Night of the Soul", None)], genres=[], moods=["Dark"])
        w2 = _work(links=[(trope_id, "The Dark Night of the Soul", "scout justification")], genres=[], moods=[])
        plan = _plan_from_data(
            [w1, w2],
            {trope_id: "The Dark Night of the Soul"},
            {w1.work_id: {trope_id}, w2.work_id: set()},
        )

        assert plan.prune_tropes == []


# --------------------------------------------------------------------------------------------
# Op-tagged token round-trip (format -> parse -> equal; fail-closed parser)
# --------------------------------------------------------------------------------------------


def _sample_plan() -> FallbackRepairPlan:
    return FallbackRepairPlan(
        delete_links=[DeleteLink(work_id=uuid4(), trope_id=uuid4(), trope_name="The Dark Night of the Soul")],
        write_slugs=[WriteSlug(work_id=uuid4(), trope_name="Dark")],
        clear_stamps=[ClearStamp(work_id=uuid4())],
        prune_tropes=[PruneTrope(trope_id=uuid4(), trope_name="The Dark Night of the Soul")],
    )


def test_token_round_trip_format_parse_equal(tmp_path):
    plan = _sample_plan()
    expected = plan_tokens(plan)

    report_path = write_report(plan, reports_dir=tmp_path)
    parsed = parse_report(report_path.read_text(encoding="utf-8"))

    assert parsed == expected


@pytest.mark.parametrize(
    "mutate, match",
    [
        (lambda lines: [line for line in lines if line != "== PLAN TOKENS =="], "PLAN TOKENS"),
        (lambda lines: [line for line in lines if line != "== END PLAN TOKENS =="], "END PLAN TOKENS"),
        (
            lambda lines: [("[delete_links 3" if line.startswith("[delete_links") else line) for line in lines],
            "malformed class-header",
        ),
    ],
    ids=["missing_start_marker", "missing_end_marker", "malformed_header_line"],
)
def test_parse_report_fails_closed(tmp_path, mutate, match):
    plan = _sample_plan()
    report_path = write_report(plan, reports_dir=tmp_path)
    lines = report_path.read_text(encoding="utf-8").splitlines()
    corrupted = "\n".join(mutate(lines))

    with pytest.raises(ValueError, match=match):
        parse_report(corrupted)


# --------------------------------------------------------------------------------------------
# Drift refusal (plan_delta)
# --------------------------------------------------------------------------------------------


def test_plan_delta_identical_plans_is_empty():
    plan = _sample_plan()
    reviewed = plan_tokens(plan)

    delta = plan_delta(reviewed, plan)

    assert all(len(v) == 0 for v in delta.values())


def test_plan_delta_extra_token_in_fresh_plan_is_flagged():
    reviewed_plan = _sample_plan()
    reviewed = plan_tokens(reviewed_plan)

    fresh_plan = FallbackRepairPlan(
        delete_links=list(reviewed_plan.delete_links),
        write_slugs=[*reviewed_plan.write_slugs, WriteSlug(work_id=uuid4(), trope_name="Extra Slug")],
        clear_stamps=list(reviewed_plan.clear_stamps),
        prune_tropes=list(reviewed_plan.prune_tropes),
    )

    delta = plan_delta(reviewed, fresh_plan)

    assert delta["write_slugs"] != set()
    assert all(len(delta[k]) == 0 for k in delta if k != "write_slugs")


def test_plan_delta_op_flip_on_same_row_is_flagged():
    """The reviewed report named this (work_id, trope_id) as a delete_link; the fresh plan
    (e.g. a concurrent write moved it) instead names it as prune_trope-relevant deletion — an
    operation flip on the same underlying ids must NOT diff as 'unchanged' just because the raw
    ids overlap. Simulated here by reviewing a delete_link for a pair and having the fresh plan
    contain a DIFFERENT op-tagged token touching the same trope_id (prune_trope), which the
    bare-id-unaware token scheme correctly treats as new."""
    trope_id = uuid4()
    work_id = uuid4()
    reviewed_plan = FallbackRepairPlan(
        delete_links=[DeleteLink(work_id=work_id, trope_id=trope_id, trope_name="The Dark Night of the Soul")]
    )
    reviewed = plan_tokens(reviewed_plan)

    # Fresh plan: the delete_link is gone (e.g. already applied or link vanished) but a NEW
    # prune_trope token for the same trope_id now appears — a flip in what's about to happen to
    # this id that a bare-id comparison would miss entirely.
    fresh_plan = FallbackRepairPlan(
        prune_tropes=[PruneTrope(trope_id=trope_id, trope_name="The Dark Night of the Soul")]
    )

    delta = plan_delta(reviewed, fresh_plan)

    assert delta["prune_tropes"] == {f"prune_trope:{trope_id}"}


def test_plan_delta_fresh_subset_of_reviewed_is_empty():
    """fresh ⊂ reviewed (some reviewed rows vanished/were already applied) is fine — no refusal,
    handled as ordinary skipped_stale at apply time, not a drift."""
    reviewed_plan = _sample_plan()
    reviewed = plan_tokens(reviewed_plan)

    fresh_plan = FallbackRepairPlan()  # everything vanished since review

    delta = plan_delta(reviewed, fresh_plan)

    assert all(len(v) == 0 for v in delta.values())
