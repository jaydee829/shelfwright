from dataclasses import dataclass

from agentic_librarian.etl import contributor_dedup as cd


@dataclass
class _Row:  # stand-in for Author/Narrator (has .name and .id)
    name: str
    id: str


def test_norm_name_collapses_case_and_whitespace():
    assert cd.norm_name("  Casualfarmer ") == cd.norm_name("casualfarmer") == "casualfarmer"
    assert cd.norm_name("Ann  Leckie") == "ann leckie"
    assert cd.norm_name(None) == ""


def test_norm_name_keeps_distinct_names_distinct():
    assert cd.norm_name("J. Smith") != cd.norm_name("John Smith")


def test_pick_survivor_prefers_cased_then_lowest_id():
    rows = [_Row("casualfarmer", "b"), _Row("Casualfarmer", "c"), _Row("casualfarmer", "a")]
    assert cd._pick_survivor(rows).name == "Casualfarmer"  # has uppercase wins
    rows_lower = [_Row("casualfarmer", "b"), _Row("casualfarmer", "a")]
    assert cd._pick_survivor(rows_lower).id == "a"  # tie -> lowest id


def test_pick_survivor_prefers_stripped_over_trailing_whitespace():
    # both have an uppercase C, so the casing key ties — the stripped name must win
    # deterministically (not by random UUID order). Higher id on the clean one proves it.
    rows = [_Row("Casualfarmer ", "a"), _Row("Casualfarmer", "z")]
    assert cd._pick_survivor(rows).name == "Casualfarmer"


def test_dup_groups_only_returns_groups_over_one():
    rows = [_Row("A", "1"), _Row("a", "2"), _Row("B", "3")]
    groups = cd._dup_groups(rows)
    assert len(groups) == 1 and {r.id for r in groups[0]} == {"1", "2"}
