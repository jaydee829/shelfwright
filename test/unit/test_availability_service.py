from agentic_librarian.availability.service import _normalize, _shape_formats

ITEMS = [
    {
        "title": "Project Hail Mary",
        "type": {"id": "ebook", "name": "eBook"},
        "isAvailable": False,
        "ownedCopies": 447,
        "availableCopies": 0,
        "holdsRatio": 6,
        "estimatedWaitDays": 83,
        "firstCreatorName": "Andy Weir",
    },
    {
        "title": "Project Hail Mary",
        "type": {"id": "audiobook", "name": "Audiobook"},
        "isAvailable": True,
        "ownedCopies": 20,
        "availableCopies": 2,
        "holdsRatio": 0,
        "estimatedWaitDays": 0,
        "firstCreatorName": "Andy Weir",
    },
    {
        "title": "Unrelated Book",
        "type": {"id": "ebook", "name": "eBook"},
        "isAvailable": True,
        "firstCreatorName": "Someone Else",
    },
]


def test_normalize():
    assert _normalize("  The   Martian ") == "the martian"


def test_shape_formats_matches_title_and_splits_by_format():
    formats = _shape_formats(ITEMS, "Project Hail Mary", "Andy Weir")
    by = {f["format"]: f for f in formats}
    assert set(by) == {"eBook", "Audiobook"}
    assert by["Audiobook"]["available"] is True
    assert by["Audiobook"]["copies_available"] == 2
    assert by["eBook"]["available"] is False
    assert by["eBook"]["wait_days"] == 83


def test_shape_formats_no_title_match_returns_empty():
    assert _shape_formats(ITEMS, "Some Other Title", "Nobody") == []


def test_shape_formats_author_mismatch_still_ok_when_title_unique():
    # Title-equality is the bar; author is a soft confirm. Wrong author but exact title → matched.
    formats = _shape_formats(ITEMS, "Project Hail Mary", "Wrong Author")
    assert {f["format"] for f in formats} == {"eBook", "Audiobook"}
