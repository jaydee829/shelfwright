"""Pure-logic unit tests for the works-merge detection classes (PR-2 part 1, Spec
2026-07-14): fold(), the series guard, per-class pair detection, unordered-pair dedup,
cross-class precedence, transitive cluster collapse, and survivor selection — all exercised
WITHOUT a live Postgres session (that's the db_integration suite's job,
test/integration/test_works_merge.py).

Lives alongside etl/dedup_backfill.py's own tests (same module, per the works-merge design
spec: "extend etl/dedup_backfill.py... do NOT build a parallel tool") but in its own test file
since the detection classes here are functionally independent of DedupPlan/plan_dedup/
apply_dedup (the pre-migration constraint gate) — see dedup_backfill.py's module docstring for
why duplicate_works_report_only/_plan_duplicate_works stay untouched.

House rule: case-driven tests are parametrized, never loops inside one test body."""

from __future__ import annotations

from uuid import uuid4

import pytest

from agentic_librarian.etl.dedup_backfill import (
    EditionMergeGroup,
    WorkMergeComposition,
    WorksMergeCluster,
    WorksMergeClusters,
    WorkStats,
    _dedupe_detected_duplicate_rows,
    _fold,
    _series_guard_blocks,
    fuzzy_similarity,
    parse_works_merge_report,
    pick_survivor,
    plan_works_merge_clusters,
    render_works_merge_apply_report,
    works_merge_delta,
    works_merge_tokens,
    write_works_merge_apply_report,
)

# --------------------------------------------------------------------------------------------
# fold()
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_name, a, b, expect_equal",
    [
        (
            "we_are_legion_punctuation_variant",
            "We Are Legion (We Are Bob)",
            "We are Legion; We are Bob",
            True,
        ),
        ("case_only", "Beware of Chicken", "beware of chicken", True),
        ("ampersand_vs_and_word", "Fire & Blood", "Fire and Blood", False),
        ("colon_subtitle", "Mistborn: The Final Empire", "Mistborn The Final Empire", True),
        ("bracket_variant", "The Song [Special Edition]", "The Song Special Edition", True),
        ("apostrophe_and_quote", "Ender's Game", 'Ender"s Game', True),
        ("trailing_bang_and_question", "Yellowface!", "Yellowface?", True),
        ("double_space_collapse", "The   Dark  Forest", "The Dark Forest", True),
        ("distinct_titles_never_equal", "Beware of Chicken", "Calling Bullshit", False),
    ],
)
def test_fold_table(case_name, a, b, expect_equal):
    assert (_fold(a) == _fold(b)) is expect_equal


# --------------------------------------------------------------------------------------------
# Series guard
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_name, base_title, variant_title, expect_blocked",
    [
        ("plain_number", "Beware of Chicken", "Beware of Chicken 2", True),
        ("roman_numeral", "Beware of Chicken", "Beware of Chicken II", True),
        ("hash_number", "Beware of Chicken", "Beware of Chicken #2", True),
        ("book_word_number", "Beware of Chicken", "Beware of Chicken Book 2", True),
        ("volume_word_number", "Beware of Chicken", "Beware of Chicken Volume 2", True),
        ("identical_titles_not_blocked", "Beware of Chicken", "Beware of Chicken", False),
        (
            "punctuation_variant_not_blocked",
            "We Are Legion (We Are Bob)",
            "We are Legion; We are Bob",
            False,
        ),
        ("unrelated_titles_not_blocked", "Beware of Chicken", "Calling Bullshit", False),
        # Gemini review (#144): volume-vs-volume — two sequels of one series must block
        # (the operator is about to read Beware of Chicken 3; it must never pair with 2).
        ("volume_vs_volume_blocked", "Beware of Chicken 2", "Beware of Chicken 3", True),
        ("volume_vs_volume_roman_blocked", "Beware of Chicken II", "Beware of Chicken 3", True),
        ("volume_vs_volume_hash_blocked", "Beware of Chicken #2", "Beware of Chicken #3", True),
        # Same volume twice = a genuine duplicate pair — must stay mergeable (folds equal).
        ("same_volume_dup_not_blocked", "Beware of Chicken 2", "Beware of Chicken 2", False),
        # Volume tokens on DIFFERENT bases: not one series — the guard must not block.
        ("volume_tokens_different_bases_not_blocked", "Beware of Chicken 2", "Mistborn 3", False),
    ],
)
def test_series_guard_table(case_name, base_title, variant_title, expect_blocked):
    assert _series_guard_blocks(base_title, variant_title) is expect_blocked


def test_series_guard_symmetric():
    """The guard must not depend on argument order — (A, B) and (B, A) agree."""
    a, b = "Beware of Chicken", "Beware of Chicken 2"
    assert _series_guard_blocks(a, b) == _series_guard_blocks(b, a)


# --------------------------------------------------------------------------------------------
# fuzzy_similarity — token-set similarity on folded titles
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_name, a, b, expect_high",
    [
        ("near_identical_word_order", "The Chicken and the Farm", "The Farm and the Chicken", True),
        ("one_extra_word", "Calling Bullshit Now", "Calling Bullshit", True),
        ("mostly_disjoint_subset", "Calling Bullshit The Art of Skepticism", "Calling Bullshit", False),
        ("totally_different", "Beware of Chicken", "Yellowface", False),
    ],
)
def test_fuzzy_similarity_directional(case_name, a, b, expect_high):
    score = fuzzy_similarity(a, b)
    assert 0.0 <= score <= 1.0
    if expect_high:
        assert score >= 0.5
    else:
        assert score < 0.5


# --------------------------------------------------------------------------------------------
# Survivor selection (pick_survivor) — deterministic tiebreak order:
# most justified trope links -> newest deep_enriched_at (NULLs last) -> most editions ->
# lowest UUID string.
# --------------------------------------------------------------------------------------------


def _stats(work_id, *, trope_links=0, deep_enriched_at=None, editions=0):
    return WorkStats(
        work_id=work_id,
        title="irrelevant",
        justified_trope_links=trope_links,
        deep_enriched_at=deep_enriched_at,
        edition_count=editions,
    )


def test_pick_survivor_most_trope_links_wins():
    lo, hi = uuid4(), uuid4()
    candidates = [_stats(lo, trope_links=1), _stats(hi, trope_links=15)]
    assert pick_survivor(candidates).work_id == hi


def test_pick_survivor_falls_back_to_newest_deep_enriched_at_on_trope_tie():
    older, newer = uuid4(), uuid4()
    import datetime as dt

    t_old = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    t_new = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    candidates = [
        _stats(older, trope_links=3, deep_enriched_at=t_old),
        _stats(newer, trope_links=3, deep_enriched_at=t_new),
    ]
    assert pick_survivor(candidates).work_id == newer


def test_pick_survivor_deep_enriched_at_nulls_last():
    never, enriched = uuid4(), uuid4()
    import datetime as dt

    candidates = [
        _stats(never, trope_links=3, deep_enriched_at=None),
        _stats(enriched, trope_links=3, deep_enriched_at=dt.datetime(2026, 1, 1, tzinfo=dt.UTC)),
    ]
    assert pick_survivor(candidates).work_id == enriched


def test_pick_survivor_falls_back_to_most_editions_on_trope_and_date_tie():
    fewer, more = uuid4(), uuid4()
    candidates = [_stats(fewer, trope_links=3, editions=1), _stats(more, trope_links=3, editions=4)]
    assert pick_survivor(candidates).work_id == more


def test_pick_survivor_falls_back_to_lowest_uuid_string_on_full_tie():
    ids = sorted([uuid4(), uuid4()], key=str)
    lowest, highest = ids[0], ids[1]
    candidates = [_stats(highest, trope_links=2, editions=2), _stats(lowest, trope_links=2, editions=2)]
    assert pick_survivor(candidates).work_id == lowest


# --------------------------------------------------------------------------------------------
# Cross-class precedence + unordered-pair dedup + transitive cluster collapse
#
# plan_works_merge_clusters is the pure composition core: given per-class pair lists (already
# computed as unordered (id, id) frozensets) and per-work stats, it dedups, resolves overlap by
# class strength, collapses transitive clusters, and returns one merge unit per cluster with its
# survivor. This is the piece H2's plan_works_merge(session) calls after gathering DB-derived
# pairs — kept DB-free here so the composition logic has a fast, deterministic test surface.
# --------------------------------------------------------------------------------------------


def test_unordered_pair_dedup_both_orders_collapse_to_one_cluster():
    a, b = uuid4(), uuid4()
    stats = {a: _stats(a, trope_links=1), b: _stats(b, trope_links=1)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[],
        same_identity_pairs=[],
        detected_duplicate_pairs=[(a, b), (b, a)],
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    assert len(clusters.detected_duplicates) == 1
    assert set(clusters.detected_duplicates[0].work_ids) == {a, b}


def test_cross_class_precedence_pair_appears_once_in_strongest_class():
    """A pair caught by BOTH works_same_isbn and works_detected_duplicates appears only in
    the stronger (same_isbn) class."""
    a, b = uuid4(), uuid4()
    stats = {a: _stats(a, trope_links=1), b: _stats(b, trope_links=1)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[(a, b)],
        same_identity_pairs=[],
        detected_duplicate_pairs=[(a, b)],
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    assert len(clusters.same_isbn) == 1
    assert len(clusters.detected_duplicates) == 0


def test_fuzzy_class_never_contains_a_pair_from_a_stronger_class():
    a, b = uuid4(), uuid4()
    stats = {a: _stats(a, trope_links=1), b: _stats(b, trope_links=1)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[(a, b)],
        same_identity_pairs=[],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[(a, b)],
        stats_by_work=stats,
    )
    assert len(clusters.same_isbn) == 1
    assert len(clusters.fuzzy_report_only) == 0


def test_transitive_cluster_collapses_into_one_cluster_one_survivor():
    """A~B via same_isbn, B~C via same_identity: one cluster {A, B, C}, one survivor."""
    a, b, c = uuid4(), uuid4(), uuid4()
    stats = {
        a: _stats(a, trope_links=1),
        b: _stats(b, trope_links=1),
        c: _stats(c, trope_links=10),  # c should win survivor
    }
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[(a, b)],
        same_identity_pairs=[(b, c)],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    all_clusters = clusters.same_isbn + clusters.same_identity
    assert len(all_clusters) == 1
    cluster = all_clusters[0]
    assert set(cluster.work_ids) == {a, b, c}
    assert cluster.survivor_id == c


def test_transitive_cluster_takes_the_strongest_class_of_any_edge():
    """A~B is same_isbn (strong), B~C is fuzzy (weak) — the merged cluster is reported under
    same_isbn, not fuzzy, and is therefore NOT report-only."""
    a, b, c = uuid4(), uuid4(), uuid4()
    stats = {a: _stats(a, trope_links=1), b: _stats(b, trope_links=1), c: _stats(c, trope_links=1)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[(a, b)],
        same_identity_pairs=[],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[(b, c)],
        stats_by_work=stats,
    )
    assert len(clusters.same_isbn) == 1
    assert set(clusters.same_isbn[0].work_ids) == {a, b, c}
    assert clusters.fuzzy_report_only == []


def test_fuzzy_only_cluster_lands_in_fuzzy_report_only():
    a, b = uuid4(), uuid4()
    stats = {a: _stats(a, trope_links=1), b: _stats(b, trope_links=1)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[],
        same_identity_pairs=[],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[(a, b)],
        stats_by_work=stats,
    )
    assert len(clusters.fuzzy_report_only) == 1
    assert clusters.same_isbn == []
    assert clusters.same_identity == []
    assert clusters.detected_duplicates == []


# --------------------------------------------------------------------------------------------
# H2 fix 1: self-referential detected_duplicates feed rows (work_id_a == work_id_b) must be
# skipped at ingestion, not fed to plan_works_merge_clusters' union-find — `frozenset((A, A))`
# has size 1, and `a, b = tuple(pair)` on a size-1 frozenset used to crash the planner.
# _dedupe_detected_duplicate_rows is the pure (session-free) helper _detect_detected_duplicate_
# pairs now delegates to, so this is testable without a live Postgres session.
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "case_name, rows_fn, expect_pair_count, expect_ignored_self",
    [
        ("no_self_pairs", lambda a, b, c: [(a, b)], 1, 0),
        ("single_self_pair_alone", lambda a, b, c: [(a, a)], 0, 1),
        ("self_pair_mixed_with_real_pair", lambda a, b, c: [(a, a), (a, b)], 1, 1),
        (
            "self_pair_plus_both_orders_of_a_real_pair_dedup_to_one",
            lambda a, b, c: [(a, a), (a, b), (b, a)],
            1,
            1,
        ),
        ("multiple_distinct_self_pairs_all_ignored", lambda a, b, c: [(a, a), (b, b), (c, c)], 0, 3),
        ("no_rows_at_all", lambda a, b, c: [], 0, 0),
    ],
)
def test_dedupe_detected_duplicate_rows_skips_self_pairs(case_name, rows_fn, expect_pair_count, expect_ignored_self):
    a, b, c = uuid4(), uuid4(), uuid4()
    pairs, ignored_self = _dedupe_detected_duplicate_rows(rows_fn(a, b, c))
    assert len(pairs) == expect_pair_count
    assert ignored_self == expect_ignored_self


def test_dedupe_detected_duplicate_rows_self_pair_never_reaches_union_find_crash():
    """The regression itself: an ungated self-pair fed straight to plan_works_merge_clusters
    used to raise ValueError unpacking a size-1 frozenset (`a, b = tuple(pair)`). Routing every
    detected_duplicates row through _dedupe_detected_duplicate_rows first (as plan_works_merge
    now does) filters the self-pair out before it ever becomes a union-find edge — this proves
    plan_works_merge_clusters never even sees the ill-shaped input, not just that the helper
    itself is safe."""
    a = uuid4()
    pairs, ignored_self = _dedupe_detected_duplicate_rows([(a, a)])
    assert pairs == []
    assert ignored_self == 1

    stats = {a: _stats(a, trope_links=1)}
    clusters = plan_works_merge_clusters(  # must not raise
        same_isbn_pairs=[],
        same_identity_pairs=[],
        detected_duplicate_pairs=pairs,
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    assert clusters.detected_duplicates == []


# --------------------------------------------------------------------------------------------
# Op-tagged token round-trip + fail-closed parser (mirrors test_fallback_repair.py's unit
# suite). Pure-dataclass fixtures — WorkMergeComposition/WorksMergeCluster/EditionMergeGroup are
# hand-built here rather than produced by compose_cluster_merge, so these run without a session
# (compose_cluster_merge's own DB-driven semantics are covered by test/integration/
# test_works_merge_apply.py).
# --------------------------------------------------------------------------------------------


def _composition(*, survivor=None, loser=None, with_edition_merge_group=False):
    survivor = survivor if survivor is not None else uuid4()
    loser = loser if loser is not None else uuid4()
    cluster = WorksMergeCluster(
        class_name="works_same_isbn", work_ids=[survivor, loser], titles=["A", "A"], survivor_id=survivor
    )
    comp = WorkMergeComposition(
        cluster=cluster,
        survivor_id=survivor,
        loser_ids=[loser],
        repoint_edition_ids=[uuid4()],
        delete_work_ids=[loser],
    )
    if with_edition_merge_group:
        mg = EditionMergeGroup(
            survivor_id=uuid4(),
            work_id=survivor,
            fmt="audiobook",
            loser_ids=[uuid4()],
            repoint_reading_history=[uuid4()],
            delete_reading_history=[uuid4()],
            repoint_narrators=[(uuid4(), uuid4())],
            delete_narrators=[(uuid4(), uuid4())],
        )
        comp.merge_editions = [mg]
    return comp


def test_token_round_trip_write_parse_equal(tmp_path):
    comp = _composition(with_edition_merge_group=True)
    expected = works_merge_tokens([comp])
    assert expected  # sanity: the fixture actually produced tokens

    report_path = write_works_merge_apply_report(WorksMergeClusters(), [comp], reports_dir=tmp_path)
    parsed = parse_works_merge_report(report_path.read_text(encoding="utf-8"))

    assert parsed == expected


@pytest.mark.parametrize(
    "mutate, match",
    [
        (
            lambda lines: [line for line in lines if line != "== END PLAN TOKENS =="],
            "END PLAN TOKENS",
        ),
        (
            lambda lines: [("[works_merge 3" if line.startswith("[works_merge]") else line) for line in lines],
            "malformed class-header",
        ),
        (
            lambda _lines: ["== PLAN IDS ==", "delete:1234", "== END PLAN IDS =="],
            "PLAN TOKENS",
        ),
    ],
    ids=["missing_end_marker", "malformed_header_line", "dedup_report_handed_to_merge_parser"],
)
def test_parse_works_merge_report_fails_closed(mutate, match):
    """Mirrors test_fallback_repair.py's test_parse_report_fails_closed. The third case is the
    works-merge-specific one: a --dedup-for-constraints report (marker '== PLAN IDS ==', not
    '== PLAN TOKENS ==') handed to parse_works_merge_report — the wrong report type for this
    gate must fail closed, not silently parse as an empty/bogus token set."""
    comp = _composition()
    report_text = render_works_merge_apply_report(WorksMergeClusters(), [comp])
    lines = report_text.splitlines()
    corrupted = "\n".join(mutate(lines))

    with pytest.raises(ValueError, match=match):
        parse_works_merge_report(corrupted)


# --------------------------------------------------------------------------------------------
# Drift delta (works_merge_delta) — addition, op-flip on the same id, survivor-flip (the
# merge_cluster binding token), and fresh subset of reviewed (no refusal).
# --------------------------------------------------------------------------------------------


def test_works_merge_delta_addition_is_flagged():
    comp = _composition()
    reviewed = works_merge_tokens([comp])

    extra_edition_id = uuid4()
    fresh_comp = _composition(survivor=comp.survivor_id, loser=comp.loser_ids[0])
    fresh_comp.repoint_edition_ids = [*comp.repoint_edition_ids, extra_edition_id]

    delta = works_merge_delta(reviewed, [fresh_comp])

    assert delta == {f"repoint_edition:{extra_edition_id}"}


def test_works_merge_delta_op_flip_on_same_id_is_flagged():
    """The reviewed report named this edition id as a whole-edition repoint; the fresh re-plan
    (e.g. a concurrent write introduced a same-format collision in the gap) instead folds it
    into a merge_edition group — an operation flip on the same underlying edition id must show
    up as a NEW token, never hidden behind an unchanged bare id."""
    survivor, loser = uuid4(), uuid4()
    edition_id = uuid4()
    cluster = WorksMergeCluster(
        class_name="works_same_isbn", work_ids=[survivor, loser], titles=["A", "A"], survivor_id=survivor
    )
    reviewed_comp = WorkMergeComposition(
        cluster=cluster, survivor_id=survivor, loser_ids=[loser], repoint_edition_ids=[edition_id]
    )
    reviewed = works_merge_tokens([reviewed_comp])

    mg = EditionMergeGroup(survivor_id=uuid4(), work_id=survivor, fmt="ebook", loser_ids=[edition_id])
    fresh_comp = WorkMergeComposition(cluster=cluster, survivor_id=survivor, loser_ids=[loser], merge_editions=[mg])

    delta = works_merge_delta(reviewed, [fresh_comp])

    assert any(t.startswith("merge_edition:") for t in delta)
    assert not any(t.startswith("repoint_edition:") for t in delta)


def test_works_merge_delta_survivor_flip_on_merge_cluster_token_is_flagged():
    """The merge_cluster:<survivor>:<losers> token is survivor-BOUND — if the deterministic
    survivor pick flips between review and apply (e.g. deep-enrichment landed on the OTHER work
    in the gap), the whole cluster's binding token changes even though the work id SET is
    unchanged, and so does which work is now the planned delete_work."""
    work_a, work_b = uuid4(), uuid4()
    cluster_a_survivor = WorksMergeCluster(
        class_name="works_same_isbn", work_ids=[work_a, work_b], titles=["A", "A"], survivor_id=work_a
    )
    reviewed_comp = WorkMergeComposition(
        cluster=cluster_a_survivor, survivor_id=work_a, loser_ids=[work_b], delete_work_ids=[work_b]
    )
    reviewed = works_merge_tokens([reviewed_comp])

    cluster_b_survivor = WorksMergeCluster(
        class_name="works_same_isbn", work_ids=[work_a, work_b], titles=["A", "A"], survivor_id=work_b
    )
    fresh_comp = WorkMergeComposition(
        cluster=cluster_b_survivor, survivor_id=work_b, loser_ids=[work_a], delete_work_ids=[work_a]
    )

    delta = works_merge_delta(reviewed, [fresh_comp])

    assert any(t.startswith("merge_cluster:") for t in delta)
    assert f"delete_work:{work_a}" in delta  # work_a flipped from survivor to loser


def test_works_merge_delta_fresh_subset_of_reviewed_is_empty():
    """fresh ⊂ reviewed (every reviewed op already applied/vanished by apply time) is fine — no
    refusal; the shrinkage is ordinary skipped_stale territory at apply time, not a drift."""
    comp = _composition(with_edition_merge_group=True)
    reviewed = works_merge_tokens([comp])

    delta = works_merge_delta(reviewed, [])  # everything vanished since review

    assert delta == set()
