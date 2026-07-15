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
    WorkStats,
    _fold,
    _series_guard_blocks,
    fuzzy_similarity,
    pick_survivor,
    plan_works_merge_clusters,
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
