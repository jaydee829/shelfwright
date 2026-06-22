import pytest

from agentic_librarian.etl import tag_cleaning as tc
from agentic_librarian.etl import tag_maps


def test_seed_maps_have_expected_entries():
    assert tag_maps.ALIAS_MAP["sci fi"] == "Science Fiction"
    assert tag_maps.ALIAS_MAP["action adventure"] == "Action & Adventure"
    assert tag_maps.ALIAS_MAP["business economics"] == "Business & Economics"
    assert tag_maps.COMBO_MAP["science fiction fantasy"] == ["Science Fiction", "Fantasy"]
    assert "audiobook" in tag_maps.DENYLIST
    assert "general" in tag_maps.DENYLIST
    assert "Fiction" in tag_maps.CONDITIONAL_DROP
    assert isinstance(tag_maps.MOOD_ALIAS_MAP, dict)
    assert isinstance(tag_maps.MOOD_DENYLIST, set)


def test_strip_uuid_and_normalize():
    assert tc._strip_uuid("science-fiction-fantasy-4c14c349-8d52-4893-aaf0-34f7e33bf275") == "science-fiction-fantasy"
    assert tc._strip_uuid("epic") == "epic"
    assert tc._normalize("Science-Fiction") == "science fiction"
    assert tc._normalize("  Business & Economics ") == "business & economics"


def test_bisac_reduce_takes_deepest_non_filler():
    assert tc._bisac_reduce("Fiction / Science Fiction / General") == "Science Fiction"
    assert tc._bisac_reduce("Fantasy") == "Fantasy"


def test_titlecase():
    assert tc._titlecase("science fiction") == "Science Fiction"
    assert tc._titlecase("business & economics") == "Business & Economics"


UUID = "4c14c349-8d52-4893-aaf0-34f7e33bf275"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ([f"science-fiction-fantasy-{UUID}"], ["Science Fiction", "Fantasy"]),
        ([f"audiobook-{UUID}"], []),
        ([f"epic-{UUID}"], ["Epic"]),
        ([f"action-adventure-{UUID}"], ["Action & Adventure"]),
        (["fiction", "Fiction", "Fantasy"], ["Fantasy"]),
        (["fiction"], ["Fiction"]),
        (["business-economics", "Business & Economics"], ["Business & Economics"]),
        (["sci-fi", "scifi", "Science-Fiction"], ["Science Fiction"]),
        ([f"general-{UUID}", "Fiction / Science Fiction / General"], ["Science Fiction"]),
        ([123, None, "Fantasy"], ["Fantasy"]),  # non-string elements dropped
        ([], []),
        (None, []),
    ],
)
def test_clean_genres(raw, expected):
    assert tc.clean_genres(raw) == expected


def test_clean_genres_is_idempotent():
    msgs = [f"science-fiction-fantasy-{UUID}", "fiction", "Fantasy", f"audiobook-{UUID}"]
    once = tc.clean_genres(msgs)
    assert tc.clean_genres(once) == once


@pytest.mark.parametrize(
    "raw,expected",
    [
        ([f"dark-{UUID}", "Dark"], ["Dark"]),  # uuid strip + case dedup
        ([f"audiobook-{UUID}"], []),  # junk dropped
        (["lighthearted", "Light-Hearted"], ["Lighthearted"]),  # alias collapse
        (["Mysterious", "reflective"], ["Mysterious", "Reflective"]),  # unknown kept, title-cased
        (["general"], []),
        (None, []),
    ],
)
def test_clean_moods(raw, expected):
    assert tc.clean_moods(raw) == expected


def test_clean_moods_no_combo_split():
    # moods never split on the genre COMBO_MAP
    assert tc.clean_moods(["science fiction fantasy"]) == ["Science Fiction Fantasy"]
