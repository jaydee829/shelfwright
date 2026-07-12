"""#111: ONE predicate for real-vs-fallback tropes (the #69-corrected semantics —
justification is NEVER consulted)."""

from agentic_librarian.etl.trope_predicate import is_fallback_trope_name


def test_genre_reencoded_trope_is_fallback():
    assert is_fallback_trope_name("Fantasy", ["Fantasy", "Romance"], ["Dark"]) is True


def test_mood_reencoded_trope_is_fallback_case_insensitive():
    assert is_fallback_trope_name("dark", ["Fantasy"], ["Dark"]) is True


def test_narrative_trope_is_real():
    assert is_fallback_trope_name("The Dark Night of the Soul", ["Fantasy"], ["Dark"]) is False


def test_junk_name_is_neither():
    # clean_trope_name("") -> [] — junk names are neither real nor fallback (None)
    assert is_fallback_trope_name("", ["Fantasy"], []) is None


def test_none_genre_mood_lists_tolerated():
    assert is_fallback_trope_name("Found Family", None, None) is False


def test_multi_slug_subset_is_fallback():
    # a slug that cleans to multiple names (via tag_maps.COMBO_MAP), ALL of which are genres/moods,
    # is a fallback (subset semantics — mirrors plan_fallback_prune's `cleaned_lower <= gm`).
    # "science-fiction-fantasy" normalizes to "science fiction fantasy", a COMBO_MAP entry that
    # splits into ["Science Fiction", "Fantasy"] regardless of casing (etl/tag_cleaning.py:99-128).
    assert is_fallback_trope_name("science-fiction-fantasy", ["Science Fiction", "Fantasy"], []) is True
