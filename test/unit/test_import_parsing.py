from agentic_librarian.imports import parsing

GOODREADS_HEADERS = [
    "Book Id", "Title", "Author", "My Rating", "Average Rating", "Binding",
    "Date Read", "Date Added", "Bookshelves", "Exclusive Shelf", "My Review",
]


def test_sniff_detects_goodreads():
    assert parsing.sniff_source(GOODREADS_HEADERS) == "goodreads"
    assert parsing.sniff_source(["title", "writer", "finished"]) == "generic"


def test_suggest_mapping_goodreads_is_the_known_map():
    m = parsing.suggest_mapping(GOODREADS_HEADERS, "goodreads")
    assert m["title"] == "Title"
    assert m["author"] == "Author"
    assert m["format"] == "Binding"
    assert m["date_completed"] == "Date Read"
    assert m["rating"] == "My Rating"
    assert m["notes"] == "My Review"
    assert m["shelf"] == "Exclusive Shelf"


def test_suggest_mapping_generic_fuzzy_matches_synonyms():
    m = parsing.suggest_mapping(["Book Title", "Writer", "Date Finished", "Stars"], "generic")
    assert m["title"] == "Book Title"
    assert m["author"] == "Writer"
    assert m["date_completed"] == "Date Finished"
    assert m["rating"] == "Stars"
    assert m["format"] is None  # no format-like column present
