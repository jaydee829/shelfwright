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
