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
    ResetWork,
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
    never touched either.

    A second, always-justified link is included on every work here so the work always has
    >=1 justified link — this isolates the ORIGINAL per-link distinguisher under test from the
    newer reset-no-evidence trigger (TestResetNoEvidence), which fires on a *different*
    condition (zero justified links anywhere on the work) and would otherwise override the
    'never_touched' cases below regardless of bogus_targets membership."""
    trope_id = uuid4()
    anchor_id = uuid4()
    work = _work(
        links=[
            (trope_id, "The Dark Night of the Soul", justification),
            (anchor_id, "Found Family", "scout justification"),
        ],
        genres=["Fantasy"],
        moods=["Dark"],
    )
    targets = {trope_id} if is_bogus_target else set()

    plan = _plan_from_data(
        [work], {trope_id: "The Dark Night of the Soul", anchor_id: "Found Family"}, {work.work_id: targets}
    )

    deleted_trope_ids = {d.trope_id for d in plan.delete_links}
    assert (trope_id in deleted_trope_ids) is expect_delete


def test_exact_name_slug_never_a_bogus_target_by_construction():
    """bogus_targets (the session-touching half, tested in the db_integration suite) never adds
    an exact-name-match trope to its result — this is enforced structurally by
    _nearest_trope_by_name returning None on an exact match before any embedding/distance
    lookup happens. Here we assert the classification core's contract: if bogus_targets_by_work
    reports NO targets for a work (the exact-name-match outcome), no delete is planned even for
    a NULL-justified link.

    A second, always-justified link keeps this work out of the reset-no-evidence trigger's
    reach (see test_delete_link_classification's docstring for why), isolating the
    bogus-targets-membership behavior under test here."""
    trope_id = uuid4()
    anchor_id = uuid4()
    work = _work(
        links=[(trope_id, "Fantasy", None), (anchor_id, "Found Family", "scout justification")],
        genres=["Fantasy"],
        moods=[],
    )

    plan = _plan_from_data([work], {trope_id: "Fantasy", anchor_id: "Found Family"}, {work.work_id: set()})

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
        but not planned for deletion) must not get a second write_slug for the same name.

        The exact-name slug link is given a justification here (a justified genre-derived link
        is a perfectly ordinary shape) so this work has >=1 justified link and stays out of the
        reset-no-evidence trigger's reach (see TestResetNoEvidence) — isolating the ORIGINAL
        per-link bogus_targets distinguisher, which is what this test is about. This claim is
        only true because of Fix 1 (review pass 3): the evidence scan considers EVERY link on
        the work, including this justified SLUG-named one — an earlier, buggy draft scoped the
        evidence scan to non-slug links only, under which this same justified "Thriller" link
        would have been silently excluded from "is there evidence," and the work's other
        NULL-justified non-slug link ("The Dark Night of the Soul") would have wrongly tripped
        the reset-no-evidence trigger instead of being governed by the per-link distinguisher
        this test isolates (see test_reset_no_evidence_trigger_scan_covers_all_links, which
        pins the corrected behavior directly)."""
        bogus_trope_id = uuid4()
        existing_slug_id = uuid4()
        work = _work(
            links=[
                (bogus_trope_id, "The Dark Night of the Soul", None),
                (existing_slug_id, "Thriller", "scout justification"),  # exact-name slug already present
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


class TestResetNoEvidence:
    """GH #70 follow-up: a work whose EVERY trope link is NULL-justified has no deep-scout
    evidence at all. Non-derivable junk residue (a link that is NOT a bogus_targets member)
    used to survive the original distinguisher, read as a real trope to has_real_remaining,
    and permanently block write_slug/clear_stamp. New trigger: >=1 link AND zero
    justified links -> ALL links planned for deletion, regardless of bogus_targets
    membership. Existing rules (justified-link gate, zero-link works untouched) must be
    unaffected."""

    def test_zero_justified_work_all_links_deleted_including_non_derivable(self):
        """3 NULL-justified links, only 1 of which is a bogus_targets member (semantically
        derivable) -> ALL 3 are planned for deletion by the new trigger, and slugs/stamp
        clearing follow from the existing deletion-triggered classes."""
        derivable_id = uuid4()
        junk_id_1 = uuid4()
        junk_id_2 = uuid4()
        work = _work(
            links=[
                (derivable_id, "The Dark Night of the Soul", None),
                (junk_id_1, "Comics Graphic Novels", None),
                (junk_id_2, "Some Other Junk", None),
            ],
            genres=["Thriller"],
            moods=["Dark"],
            deep_enriched_at="2026-01-01T00:00:00Z",
        )
        all_tropes_by_id = {
            derivable_id: "The Dark Night of the Soul",
            junk_id_1: "Comics Graphic Novels",
            junk_id_2: "Some Other Junk",
        }
        # only derivable_id is a bogus_targets member; the two junk tropes are NOT derivable
        # from this work's genres/moods at all.
        plan = _plan_from_data([work], all_tropes_by_id, {work.work_id: {derivable_id}})

        deleted_trope_ids = {d.trope_id for d in plan.delete_links if d.work_id == work.work_id}
        assert deleted_trope_ids == {derivable_id, junk_id_1, junk_id_2}

        slug_names = {s.trope_name for s in plan.write_slugs if s.work_id == work.work_id}
        assert slug_names == {"Thriller", "Dark"}
        assert [c.work_id for c in plan.clear_stamps] == [work.work_id]

    def test_at_least_one_justified_link_never_triggers_full_reset(self):
        """1 justified + 2 NULL-just links (1 of the NULL-just links derivable, 1 not) ->
        the new trigger must NOT fire (there IS a justified link); only the original
        semantic-recompute trigger applies, so only the derivable NULL-just link is deleted.
        The justified link survives, so no slug/stamp actions follow."""
        justified_id = uuid4()
        derivable_id = uuid4()
        junk_id = uuid4()
        work = _work(
            links=[
                (justified_id, "Found Family", "scout justification"),
                (derivable_id, "The Dark Night of the Soul", None),
                (junk_id, "Comics Graphic Novels", None),
            ],
            genres=["Thriller"],
            moods=["Dark"],
            deep_enriched_at="2026-01-01T00:00:00Z",
        )
        all_tropes_by_id = {
            justified_id: "Found Family",
            derivable_id: "The Dark Night of the Soul",
            junk_id: "Comics Graphic Novels",
        }
        plan = _plan_from_data([work], all_tropes_by_id, {work.work_id: {derivable_id}})

        deleted_trope_ids = {d.trope_id for d in plan.delete_links if d.work_id == work.work_id}
        assert deleted_trope_ids == {derivable_id}

        # the justified link survives -> no write_slug/clear_stamp for this work
        assert plan.write_slugs == []
        assert plan.clear_stamps == []

    def test_zero_link_work_never_triggers_reset(self):
        """A work with ZERO links must never trigger the new reset (the trigger explicitly
        requires >=1 link) — the #67 fast-pass tropeless works stay invisible."""
        work = _work(
            links=[],
            genres=["Thriller"],
            moods=["Dark"],
            deep_enriched_at="2026-01-01T00:00:00Z",
        )
        plan = _plan_from_data([work], {}, {work.work_id: set()})

        assert plan.delete_links == []
        assert plan.write_slugs == []
        assert plan.clear_stamps == []

    def test_reset_no_evidence_trigger_scan_covers_all_links(self):
        """Fix 1 (Critical, adjudicated review pass 3): the evidence scan must consider EVERY
        link of the work, including slug-named ones — a justified slug-named link IS evidence
        and must block the reset trigger entirely. Here the work has one JUSTIFIED slug-named
        link ("Thriller", justification set, name is a member of the work's own genres — an
        entirely ordinary justified genre-derived slug link) plus one NULL-justified,
        non-derivable, non-slug link ("Some Junk", not a bogus_targets member and not a
        slug). An earlier, buggy draft scoped the evidence scan to non-slug links only, under
        which the justified slug link would have been silently excluded from 'is there
        evidence' and this work would have wrongly tripped the reset trigger, force-deleting
        the junk link and reporting the work as reset. The corrected scan sees the justified
        slug link as evidence -> the trigger must NOT fire at all: no delete_link (the junk
        link is neither a bogus_targets member nor a slug so the ORIGINAL per-link trigger
        also leaves it alone), no write_slug, no clear_stamp, and reset_works stays empty."""
        thriller_slug_id = uuid4()
        junk_id = uuid4()
        work = _work(
            links=[
                (thriller_slug_id, "Thriller", "scout: genre-derived, confirmed"),
                (junk_id, "Some Junk", None),
            ],
            genres=["Thriller"],
            moods=[],
            deep_enriched_at="2026-01-01T00:00:00Z",
        )
        all_tropes_by_id = {thriller_slug_id: "Thriller", junk_id: "Some Junk"}
        # junk_id is NOT a bogus_targets member (non-derivable from this work's own tags).
        plan = _plan_from_data([work], all_tropes_by_id, {work.work_id: set()})

        assert plan.delete_links == []
        assert plan.write_slugs == []
        assert plan.clear_stamps == []
        assert plan.reset_works == []

    def test_already_reset_work_converges_to_nothing_on_replan(self):
        """Convergence (found via the db_integration round-trip, not in the original design
        note): write_slug ALWAYS writes its exact-name slug links with justification=None, so
        a work that was JUST reset would, without the fallback-name carve-out, still have zero
        justified links on the very next re-plan and get its brand-new legitimate slugs
        deleted-and-rewritten forever. Simulates that post-apply state directly: a work whose
        ONLY links are exact-name genre/mood slugs (NULL-justified, as write_slug always writes
        them) must plan NOTHING — not a delete, not a re-trigger of the reset, not a duplicate
        slug."""
        thriller_slug_id = uuid4()
        dark_slug_id = uuid4()
        work = _work(
            links=[
                (thriller_slug_id, "Thriller", None),
                (dark_slug_id, "Dark", None),
            ],
            genres=["Thriller"],
            moods=["Dark"],
            deep_enriched_at=None,  # already cleared by the prior reset's clear_stamp
        )
        all_tropes_by_id = {thriller_slug_id: "Thriller", dark_slug_id: "Dark"}
        plan = _plan_from_data([work], all_tropes_by_id, {work.work_id: set()})

        assert plan.delete_links == []
        assert plan.write_slugs == []
        assert plan.clear_stamps == []
        assert plan.reset_works == []

    def test_already_reset_work_with_combo_map_genre_converges_to_nothing_on_replan(self):
        """Fix 2 (Important, adjudicated review pass 3): the slug carve-out must be keyed to
        write_slug's EXACT output — _cleaned_tag_names(genres, moods) — not the shared
        trope_predicate.is_fallback_trope_name (which checks a cleaned name against the work's
        RAW, uncleaned genres|moods and therefore never accounts for COMBO_MAP splits). Raw
        genre tag "Science Fiction Fantasy" is a COMBO_MAP entry that cleans to two exact-name
        slugs, ["Science Fiction", "Fantasy"] (tag_maps.COMBO_MAP["science fiction fantasy"]) —
        exactly what write_slug would have written as this work's post-reset shape. Simulates
        that post-apply state directly (both links present, NULL-justified, as write_slug
        always writes them, deep_enriched_at already cleared): re-plan must plan NOTHING for
        this work — no delete, no re-trigger of the reset, no duplicate slug. Under the OLD
        is_fallback_trope_name-based carve-out, neither "Science Fiction" nor "Fantasy" would
        match the raw genre string "Science Fiction Fantasy" as a set member, so both would be
        misclassified as non-slug 'real' tropes: has_real_remaining would see them as
        real-remaining evidence (permanently blocking write_slug/clear_stamp), and — because
        they're also NULL-justified — the reset-no-evidence scan would count them as non-slug
        'evidence' too, wrongly re-triggering a full reset on this already-correctly-shaped
        work forever."""
        sci_fi_slug_id = uuid4()
        fantasy_slug_id = uuid4()
        work = _work(
            links=[
                (sci_fi_slug_id, "Science Fiction", None),
                (fantasy_slug_id, "Fantasy", None),
            ],
            genres=["Science Fiction Fantasy"],
            moods=[],
            deep_enriched_at=None,  # already cleared by the prior reset's clear_stamp
        )
        all_tropes_by_id = {sci_fi_slug_id: "Science Fiction", fantasy_slug_id: "Fantasy"}
        plan = _plan_from_data([work], all_tropes_by_id, {work.work_id: set()})

        assert plan.delete_links == []
        assert plan.write_slugs == []
        assert plan.clear_stamps == []
        assert plan.reset_works == []


class TestStampOnlyClass:
    """Fix 3 (Important, adjudicated review pass 3): a stamped work with >=1 link, ZERO
    justified links anywhere, and ALL links slug-named (per Fix 2's _is_slug_link) is a
    migration-backfill false positive whose fallback representation is already correct — plan
    clear_stamp ONLY (no deletes, no slug writes). This traces case (a) from the review
    directly: a work whose only links are the exact-name "Thriller"/"Dark" slugs write_slug
    would have written, all NULL-justified, with a deep_enriched_at stamp still set (the 6.3
    migration backfill stamped ANY work with >=1 trope row, without regard to whether those
    rows were fallback slugs)."""

    @pytest.mark.parametrize(
        "deep_enriched_at, expect_clear_stamp, expect_reset_work",
        [
            ("2026-01-01T00:00:00Z", True, True),
            (None, False, False),
        ],
        ids=["stamped_clears_stamp_only", "unstamped_plans_nothing"],
    )
    def test_slug_only_zero_justified_work(self, deep_enriched_at, expect_clear_stamp, expect_reset_work):
        thriller_slug_id = uuid4()
        dark_slug_id = uuid4()
        work = _work(
            links=[
                (thriller_slug_id, "Thriller", None),
                (dark_slug_id, "Dark", None),
            ],
            genres=["Thriller"],
            moods=["Dark"],
            deep_enriched_at=deep_enriched_at,
        )
        all_tropes_by_id = {thriller_slug_id: "Thriller", dark_slug_id: "Dark"}
        plan = _plan_from_data([work], all_tropes_by_id, {work.work_id: set()})

        # never any deletes or slug writes for this shape, stamped or not
        assert plan.delete_links == []
        assert plan.write_slugs == []

        assert ([c.work_id for c in plan.clear_stamps] == [work.work_id]) is expect_clear_stamp
        if not expect_clear_stamp:
            assert plan.clear_stamps == []

        reset_work_ids = {r.work_id for r in plan.reset_works}
        assert (work.work_id in reset_work_ids) is expect_reset_work
        if expect_reset_work:
            (reset_entry,) = [r for r in plan.reset_works if r.work_id == work.work_id]
            assert reset_entry.stamp_only is True
        else:
            assert plan.reset_works == []


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
        reset_works=[ResetWork(work_id=uuid4(), title="Lessons in Chemistry")],
    )


@pytest.mark.parametrize(
    "db_target",
    [None, "…@prod-host/shelfwright"],
    ids=["no_db_target", "with_db_target_header"],
)
def test_token_round_trip_format_parse_equal(tmp_path, db_target):
    """The optional `db target: ...` header (final whole-branch review fix, DB-target
    visibility) is written ABOVE the '== PLAN TOKENS ==' block as a plain human-readable
    line — parse_report's fail-closed parser only looks between the start/end markers, so
    the header must never appear in (or otherwise corrupt) the parsed token set.

    _sample_plan() also carries a reset_works entry (#70 follow-up) — the report's
    'reset (no-evidence) works' section is informational only (ABOVE/OUTSIDE the token
    block, like the db-target header), so its presence must not affect the parsed token set
    either."""
    plan = _sample_plan()
    expected = plan_tokens(plan)

    report_path = write_report(plan, reports_dir=tmp_path, db_target=db_target)
    report_text = report_path.read_text(encoding="utf-8")
    parsed = parse_report(report_text)

    assert parsed == expected
    if db_target is not None:
        assert f"db target: {db_target}" in report_text.splitlines()
        # the header line must land before the token block, never inside it
        header_line_idx = report_text.splitlines().index(f"db target: {db_target}")
        token_start_idx = report_text.splitlines().index("== PLAN TOKENS ==")
        assert header_line_idx < token_start_idx

    report_lines = report_text.splitlines()
    reset_section_idx = next(i for i, line in enumerate(report_lines) if line.startswith("reset (no-evidence) works"))
    token_start_idx = report_lines.index("== PLAN TOKENS ==")
    assert reset_section_idx < token_start_idx
    assert f"  work_id={plan.reset_works[0].work_id}  title='Lessons in Chemistry'" in report_lines


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
