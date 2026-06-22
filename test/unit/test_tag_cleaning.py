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


from agentic_librarian.etl import tag_cleaning as tc


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
