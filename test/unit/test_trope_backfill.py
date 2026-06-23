"""Pure-function unit tests for trope_backfill (the migration logic itself is db_integration)."""

from agentic_librarian.etl.trope_backfill import _fold_score


def test_fold_score_takes_higher():
    assert _fold_score(0.9, 0.5) == 0.9
    assert _fold_score(0.4, 0.6) == 0.6


def test_fold_score_ignores_none():
    assert _fold_score(None, 0.5) == 0.5
    assert _fold_score(0.7, None) == 0.7


def test_fold_score_both_none_falls_back_to_default():
    # relevance_score is nullable=False, default 1.0 — never return None
    assert _fold_score(None, None) == 1.0
