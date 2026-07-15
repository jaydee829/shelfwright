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

import math
from uuid import uuid4

import pytest

from agentic_librarian.etl.dedup_backfill import (
    EditionMergeGroup,
    WorkMergeComposition,
    WorksMergeCluster,
    WorksMergeClusters,
    WorkStats,
    _classify_isbn_group_pairs,
    _dedupe_detected_duplicate_rows,
    _detect_fuzzy_pairs,
    _fold,
    _series_guard_blocks,
    applyable_works_merge_clusters,
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
# H3 hardening (2026-07-15, real prod dry-run): _classify_isbn_group_pairs — an ISBN group's
# pairwise classification into applyable (folded titles agree) vs mismatch (folded titles
# disagree — report-only, never applied). The prod dry-run's real false-merge shapes are the
# fixtures here: a 14-book "Ender chain" (one bogus shared ISBN, all titles distinct — must
# produce ZERO applyable pairs), the Beware-of-Chicken-shaped group (a genuine same-ISBN
# duplicate pair PLUS a sequel sharing its predecessor's ISBN), and a real typo pair (shared
# ISBN, near-identical but non-equal folded titles).
# --------------------------------------------------------------------------------------------


def _fold_ids(*titles: str) -> tuple[list, dict]:
    ids = [uuid4() for _ in titles]
    fold_by_work = {wid: _fold(title) for wid, title in zip(ids, titles, strict=True)}
    return ids, fold_by_work


@pytest.mark.parametrize(
    "case_name, titles, expect_applyable_count",
    [
        ("typo_pair_exit_strategy", ["Exit Stategy", "Exit Strategy"], 0),
        (
            "beware_group_one_applyable_pair_plus_sequel",
            ["Beware of Chicken", "Beware of Chicken", "Beware of Chicken 2"],
            1,
        ),
        (
            "ender_chain_fourteen_distinct_titles_zero_applyable",
            [
                "Ender's Game",
                "Ender's Shadow",
                "Shadow of the Hegemon",
                "The Hunger of the Gods",
                "Fury of the Gods",
                "The Silence of Unworthy Gods",
                "The Gate of the Feral Gods",
                "Shadow of the Giant",
                "The Fury",
                "The Shadow of the Gods",
                "The Shadow",
                "The Brightest Shadow",
                "The Shadow Cabinet",
                "The Gate of the Feral Gods: Dungeon Crawler Carl Book 4",
            ],
            0,
        ),
    ],
)
def test_classify_isbn_group_pairs_applyable_count(case_name, titles, expect_applyable_count):
    ids, fold_by_work = _fold_ids(*titles)
    applyable, mismatch = _classify_isbn_group_pairs(ids, fold_by_work)
    assert len(applyable) == expect_applyable_count
    # every pairwise combination is classified exactly once, into exactly one of the two lists
    assert len(applyable) + len(mismatch) == math.comb(len(titles), 2)


def test_classify_isbn_group_pairs_raises_on_unknown_work_id():
    """Gemini review (#146): an id missing from fold_by_work must FAIL LOUD (KeyError), never
    match None == None into an applyable pair. The edition scan filters unknown ids before
    grouping (test below); this pins the honest-failure contract if that filter ever regresses."""
    ids, fold_by_work = _fold_ids("Beware of Chicken")
    stranger = uuid4()  # never registered in fold_by_work
    with pytest.raises(KeyError):
        _classify_isbn_group_pairs([ids[0], stranger], fold_by_work)


def test_detect_same_isbn_pairs_skips_works_unknown_to_the_plan():
    """A work created between the plan's works scan and its edition scan (no fold/stats entry)
    is skipped — it becomes next plan run's problem instead of a None-matched applyable pair
    or a KeyError at cluster build (Gemini review, #146)."""
    from unittest.mock import MagicMock

    from agentic_librarian.etl.dedup_backfill import _detect_same_isbn_pairs

    known_a, known_b = uuid4(), uuid4()
    stranger = uuid4()
    fold_by_work = {known_a: _fold("Beware of Chicken"), known_b: _fold("Beware of Chicken")}
    session = MagicMock()
    session.query.return_value.filter.return_value.all.return_value = [
        (known_a, "9781039452275"),
        (known_b, "9781039452275"),
        (stranger, "9781039452275"),  # mid-plan newcomer: same ISBN, unknown to the plan
        (stranger, "9990000000000"),  # and alone on another ISBN
    ]
    applyable, mismatch = _detect_same_isbn_pairs(session, fold_by_work)
    assert applyable == [tuple(sorted((known_a, known_b), key=str))]
    assert mismatch == []  # the stranger produced neither applyable nor mismatch pairs


def test_classify_isbn_group_pairs_beware_shaped_sequel_never_applyable():
    """The prod shape exactly: two equal-fold 'Beware of Chicken' works pair up applyable; the
    differently-folded 'Beware of Chicken 2' sequel pairs with EACH of them as mismatch — never
    applyable, and visible against every non-matching member it shares the ISBN group with."""
    ids, fold_by_work = _fold_ids("Beware of Chicken", "Beware of Chicken", "Beware of Chicken 2")
    boc_a, boc_b, boc_2 = ids
    applyable, mismatch = _classify_isbn_group_pairs(ids, fold_by_work)

    assert {frozenset(p) for p in applyable} == {frozenset((boc_a, boc_b))}
    assert {frozenset(p) for p in mismatch} == {frozenset((boc_a, boc_2)), frozenset((boc_b, boc_2))}


def test_classify_isbn_group_pairs_typo_pair_is_mismatch_only():
    ids, fold_by_work = _fold_ids("Exit Stategy", "Exit Strategy")
    applyable, mismatch = _classify_isbn_group_pairs(ids, fold_by_work)
    assert applyable == []
    assert {frozenset(p) for p in mismatch} == {frozenset(ids)}


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
    """Among the THREE applyable classes only: A~B is same_isbn (strong), B~C is same_identity
    (also applyable) — the merged cluster is reported under same_isbn (the strongest applyable
    edge), still one cluster of all three."""
    a, b, c = uuid4(), uuid4(), uuid4()
    stats = {a: _stats(a, trope_links=1), b: _stats(b, trope_links=1), c: _stats(c, trope_links=1)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[(a, b)],
        same_identity_pairs=[(b, c)],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    assert len(clusters.same_isbn) == 1
    assert set(clusters.same_isbn[0].work_ids) == {a, b, c}


def test_fuzzy_edge_contagion_regression():
    """H3 CONTRACT REVERSAL (2026-07-15): A~B is same_isbn (applyable), B~C is fuzzy
    (report-only). Before the fix, ONE shared union-find let this fuzzy edge pull C into the
    applyable same_isbn cluster (the exact amplification the prod dry-run caught). After the
    fix: the applyable cluster stays EXACTLY {A, B}; C shows up only in the fuzzy report-only
    cluster, and applyable_works_merge_clusters (the sole choke point apply reads through) never
    contains C."""
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
    assert set(clusters.same_isbn[0].work_ids) == {a, b}

    fuzzy_ids = {wid for cluster in clusters.fuzzy_report_only for wid in cluster.work_ids}
    assert c in fuzzy_ids

    applyable_ids = {wid for cluster in applyable_works_merge_clusters(clusters) for wid in cluster.work_ids}
    assert c not in applyable_ids
    assert applyable_ids == {a, b}


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
# H3 hardening (2026-07-15): works_same_isbn_title_mismatch is a report-only class that clusters
# in its OWN independent union-find — it can never join, grow, or relabel an applyable cluster.
# The prod dry-run's real false-merge shapes are the fixtures: the Ender chain (14 distinct
# books chained by one bogus shared ISBN — must produce ZERO applyable clusters), the
# Beware-of-Chicken-shaped group (a real duplicate pair PLUS a sequel sharing the predecessor's
# ISBN — the sequel must never enter an applyable cluster), and a real typo pair (report-only,
# operator promotes by hand).
# --------------------------------------------------------------------------------------------


def test_isbn_mismatch_only_pair_never_applyable_typo_pair_shaped():
    """'Exit Stategy'/'Exit Strategy'-shaped: shared ISBN, differing folds — mismatch report-only,
    never in works_same_isbn, never applyable."""
    a, b = uuid4(), uuid4()
    stats = {a: _stats(a, trope_links=1), b: _stats(b, trope_links=1)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[],
        same_isbn_mismatch_pairs=[(a, b)],
        same_identity_pairs=[],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    assert clusters.same_isbn == []
    assert len(clusters.same_isbn_title_mismatch) == 1
    assert set(clusters.same_isbn_title_mismatch[0].work_ids) == {a, b}
    assert applyable_works_merge_clusters(clusters) == []


def test_beware_shaped_group_one_applyable_pair_sequel_never_applyable():
    """The prod shape exactly: {BoC, BoC, BoC2} within one ISBN group — one applyable pair (the
    two equal-fold BoC works) and mismatch report entries for the sequel; the sequel must NEVER
    appear in an applyable cluster."""
    boc_a, boc_b, boc_2 = uuid4(), uuid4(), uuid4()
    stats = {
        boc_a: _stats(boc_a, trope_links=1, editions=1),
        boc_b: _stats(boc_b, trope_links=15, editions=2),
        boc_2: _stats(boc_2, trope_links=7, editions=1),
    }
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[(boc_a, boc_b)],
        same_isbn_mismatch_pairs=[(boc_a, boc_2), (boc_b, boc_2)],
        same_identity_pairs=[],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    assert len(clusters.same_isbn) == 1
    assert set(clusters.same_isbn[0].work_ids) == {boc_a, boc_b}

    mismatch_ids = {wid for cluster in clusters.same_isbn_title_mismatch for wid in cluster.work_ids}
    assert boc_2 in mismatch_ids

    applyable_ids = {wid for cluster in applyable_works_merge_clusters(clusters) for wid in cluster.work_ids}
    assert boc_2 not in applyable_ids
    assert applyable_ids == {boc_a, boc_b}


def test_ender_shaped_chain_zero_applyable_clusters():
    """The prod dry-run's actual false-merge shape: N books all sharing one bogus ISBN, all
    titles distinct -> every pairwise combination in the group is a mismatch, ZERO applyable
    pairs, ZERO applyable clusters. Fed straight from _classify_isbn_group_pairs' real output so
    this test exercises the same shape plan_works_merge would build from a live DB scan."""
    titles = [
        "Ender's Game",
        "Ender's Shadow",
        "Shadow of the Hegemon",
        "The Hunger of the Gods",
        "Fury of the Gods",
        "The Silence of Unworthy Gods",
        "The Gate of the Feral Gods",
        "Shadow of the Giant",
        "The Fury",
        "The Shadow of the Gods",
        "The Shadow",
        "The Brightest Shadow",
        "The Shadow Cabinet",
        "The Gate of the Feral Gods: Dungeon Crawler Carl Book 4",
    ]
    ids = [uuid4() for _ in titles]
    fold_by_work = {wid: _fold(title) for wid, title in zip(ids, titles, strict=True)}
    applyable_pairs, mismatch_pairs = _classify_isbn_group_pairs(ids, fold_by_work)
    assert applyable_pairs == []

    stats = {wid: _stats(wid, trope_links=1) for wid in ids}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=applyable_pairs,
        same_isbn_mismatch_pairs=mismatch_pairs,
        same_identity_pairs=[],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    assert clusters.same_isbn == []
    assert applyable_works_merge_clusters(clusters) == []
    # Visible for operator triage, not silently dropped.
    mismatch_ids = {wid for cluster in clusters.same_isbn_title_mismatch for wid in cluster.work_ids}
    assert mismatch_ids == set(ids)


def test_mismatch_pair_dropped_when_both_members_already_in_one_applyable_cluster():
    """A report-only pair is only useful for triage if it points somewhere NEW — if both its
    endpoints already sit together in one applyable cluster (e.g. same_isbn joined A-B, and
    same_identity separately joined B-C into the SAME applyable cluster), a mismatch edge
    between A and C adds nothing and must be dropped entirely, not shown as a redundant cluster."""
    a, b, c = uuid4(), uuid4(), uuid4()
    stats = {a: _stats(a, trope_links=1), b: _stats(b, trope_links=1), c: _stats(c, trope_links=1)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[(a, b)],
        same_isbn_mismatch_pairs=[(a, c)],
        same_identity_pairs=[(b, c)],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    assert set(clusters.same_isbn[0].work_ids) == {a, b, c}
    assert clusters.same_isbn_title_mismatch == []


def test_mismatch_pair_kept_when_spanning_two_different_applyable_clusters():
    """A mismatch pair whose two endpoints sit in TWO DIFFERENT applyable clusters (not one) is
    still real triage information — kept for display, its own independent report-only cluster."""
    a, b, c, d = uuid4(), uuid4(), uuid4(), uuid4()
    stats = {x: _stats(x, trope_links=1) for x in (a, b, c, d)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[(a, b), (c, d)],
        same_isbn_mismatch_pairs=[(a, c)],
        same_identity_pairs=[],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[],
        stats_by_work=stats,
    )
    assert {frozenset(cl.work_ids) for cl in clusters.same_isbn} == {frozenset((a, b)), frozenset((c, d))}
    assert len(clusters.same_isbn_title_mismatch) == 1
    assert set(clusters.same_isbn_title_mismatch[0].work_ids) == {a, c}


def test_mismatch_and_fuzzy_cluster_independently_never_share_a_union_find():
    """A same_isbn_mismatch pair (A, B) and a fuzzy pair (B, C) share endpoint B but must NOT
    merge into one report-only cluster — each report-only class clusters in its OWN independent
    union-find (never each other's, per the module contract)."""
    a, b, c = uuid4(), uuid4(), uuid4()
    stats = {a: _stats(a, trope_links=1), b: _stats(b, trope_links=1), c: _stats(c, trope_links=1)}
    clusters = plan_works_merge_clusters(
        same_isbn_pairs=[],
        same_isbn_mismatch_pairs=[(a, b)],
        same_identity_pairs=[],
        detected_duplicate_pairs=[],
        fuzzy_pairs=[(b, c)],
        stats_by_work=stats,
    )
    assert len(clusters.same_isbn_title_mismatch) == 1
    assert set(clusters.same_isbn_title_mismatch[0].work_ids) == {a, b}
    assert len(clusters.fuzzy_report_only) == 1
    assert set(clusters.fuzzy_report_only[0].work_ids) == {b, c}


# --------------------------------------------------------------------------------------------
# H3 hardening: _detect_fuzzy_pairs must exclude pairs already caught by works_same_isbn_title_
# mismatch — a differing-title ISBN pair is already visible under that class and should not ALSO
# show up as a fuzzy pair.
# --------------------------------------------------------------------------------------------


def test_detect_fuzzy_pairs_excludes_mismatch_pair_via_already_paired():
    a, b = uuid4(), uuid4()
    title_by_work = {a: "Exit Stategy", b: "Exit Strategy"}
    already_paired = {frozenset((a, b))}  # as plan_works_merge now seeds it from same_isbn_mismatch_pairs

    pairs = _detect_fuzzy_pairs(title_by_work, already_paired)

    assert pairs == []


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
