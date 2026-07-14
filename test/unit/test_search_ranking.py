"""Unit tests for the ranked candidate-pool selection in search_internal_database (#125).

The pool-selection SQL is pgvector-only, so per CLAUDE.md rule 4 the statements are
compile-inspected against the postgresql dialect here; test_internal_retrieval.py executes
them for real under the db_integration marker. The drop/demote exclusion logic is pure
Python and tested directly."""

from __future__ import annotations

import pytest
from sqlalchemy.dialects import postgresql

from agentic_librarian.mcp import server

VEC = [0.1] * 1536
IDS = ["11111111-1111-4111-8111-111111111111", "22222222-2222-4222-8222-222222222222"]


def _compiled(stmt) -> str:
    return str(stmt.compile(dialect=postgresql.dialect()))


def test_trope_rank_select_orders_by_score_before_limiting():
    sql = _compiled(server._trope_rank_select(VEC, IDS, pool_limit=30))
    assert "GROUP BY work_tropes.work_id" in sql
    assert "<=>" in sql  # pgvector cosine distance
    assert "relevance_score" in sql  # low-relevance links are penalized in the pool score
    assert "ORDER BY" in sql and "LIMIT" in sql
    assert sql.index("ORDER BY") < sql.index("LIMIT")  # ranked INTO the pool, not after it


def test_work_style_rank_select_orders_by_score_before_limiting():
    sql = _compiled(server._work_style_rank_select(VEC, IDS, pool_limit=30))
    assert "GROUP BY work_styles.work_id" in sql
    assert "<=>" in sql
    assert sql.index("ORDER BY") < sql.index("LIMIT")


def test_author_style_rank_select_reaches_works_through_contributors():
    sql = _compiled(server._author_style_rank_select(VEC, IDS, pool_limit=30))
    assert "work_contributors" in sql and "author_styles" in sql
    assert "GROUP BY work_contributors.work_id" in sql
    assert sql.index("ORDER BY") < sql.index("LIMIT")


@pytest.mark.parametrize(
    "builder",
    [server._trope_rank_select, server._work_style_rank_select, server._author_style_rank_select],
)
def test_rank_select_excludes_work_ids_when_given(builder):
    sql = _compiled(builder(VEC, IDS, pool_limit=30, exclude_work_ids=IDS))
    assert "NOT IN" in sql


@pytest.mark.parametrize(
    "builder",
    [server._trope_rank_select, server._work_style_rank_select, server._author_style_rank_select],
)
def test_rank_select_omits_exclusion_by_default(builder):
    sql = _compiled(builder(VEC, IDS, pool_limit=30))
    assert "NOT IN" not in sql


def test_neg_trope_distance_select_returns_min_distance_per_work():
    sql = _compiled(server._neg_trope_distance_select(VEC, IDS))
    assert "min" in sql.lower() and "<=>" in sql
    assert "GROUP BY work_tropes.work_id" in sql
    assert "LIMIT" not in sql  # measures every candidate; never truncates


def test_merge_min_keeps_best_score_per_work():
    merged = server._merge_min({"a": 0.5, "b": 0.2}, {"a": 0.3, "c": 0.9})
    assert merged == {"a": 0.3, "b": 0.2, "c": 0.9}


@pytest.mark.parametrize(
    ("pos", "neg", "expected"),
    [
        # No negatives: pure positive-score order.
        ({"a": 0.2, "b": 0.1}, {}, ["b", "a"]),
        # Closer to a negative than to any positive target: dropped. pos_scores carry the
        # relevance penalty while neg distances are raw — deliberate asymmetry: weak positive
        # evidence earns less protection from a user's exclusion.
        ({"a": 0.2, "b": 0.1}, {"a": 0.05}, ["b"]),
        # Near a negative (within the demote margin) but still closer to the positive:
        # kept, demoted below every clean candidate despite a better positive score.
        ({"a": 0.10, "b": 0.40}, {"a": 0.30}, ["b", "a"]),
        # Far from all negatives: untouched ordering.
        ({"a": 0.2, "b": 0.1}, {"a": 0.9, "b": 0.8}, ["b", "a"]),
        # Equal distances resolve as a drop (ties go to the exclusion — feedback wins).
        ({"a": 0.3}, {"a": 0.3}, []),
        # Mixed: c dropped, a demoted, b clean.
        ({"a": 0.10, "b": 0.15, "c": 0.20}, {"a": 0.30, "c": 0.10}, ["b", "a"]),
    ],
)
def test_apply_exclusions_drop_demote_keep(pos, neg, expected):
    assert server._apply_exclusions(pos, neg) == expected
