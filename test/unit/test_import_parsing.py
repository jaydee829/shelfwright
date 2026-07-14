from datetime import date

from agentic_librarian.imports import parsing

GOODREADS_HEADERS = [
    "Book Id",
    "Title",
    "Author",
    "My Rating",
    "Average Rating",
    "Binding",
    "Date Read",
    "Date Added",
    "Bookshelves",
    "Exclusive Shelf",
    "My Review",
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


def test_suggest_mapping_avoids_substring_false_positives():
    m = parsing.suggest_mapping(["Subtitle", "Author", "Unfinished"], "generic")
    assert m["title"] is None  # 'Subtitle' must not match the 'title' synonym
    assert m["author"] == "Author"
    assert m["date_completed"] is None  # 'Unfinished' must not match the 'finished' synonym


def test_suggest_mapping_by_synonym_requires_whole_word():
    m = parsing.suggest_mapping(["Standby", "Title"], "generic")
    assert m["author"] is None  # 'Standby' must not match the short 'by' synonym
    assert m["title"] == "Title"


def test_parse_rows_normalizes_format_rating_date_shelf():
    mapping = parsing.suggest_mapping(GOODREADS_HEADERS, "goodreads")
    rows = [
        {
            "Title": "Dune",
            "Author": "Frank Herbert",
            "Binding": "Kindle Edition",
            "Date Read": "2024/03/05",
            "My Rating": "5",
            "My Review": "great",
            "Exclusive Shelf": "read",
        },
        {
            "Title": "Unrated",
            "Author": "A B",
            "Binding": "Audiobook",
            "Date Read": "",
            "My Rating": "0",
            "My Review": "",
            "Exclusive Shelf": "to-read",
        },
    ]
    parsed = parsing.parse_rows(rows, mapping)

    assert parsed[0].raw_format == "ebook"  # Kindle Edition -> ebook
    assert parsed[0].rating == 5
    assert parsed[0].date_completed == date(2024, 3, 5)
    assert parsed[0].bad_date is False
    assert parsed[0].shelf == "read"

    assert parsed[1].raw_format == "audiobook"
    assert parsed[1].rating is None  # 0 -> unrated
    assert parsed[1].date_completed is None
    assert parsed[1].bad_date is False  # blank date is not "bad"
    assert parsed[1].shelf == "to-read"


def test_parse_rows_accepts_datetime_bearing_and_month_name_dates():
    # Libby exports a "timestamp" column like "October 14, 2017 0:34" (naive local time,
    # non-zero-padded hour); only the calendar date matters (GH user report 2026-07-14).
    mapping = {
        "title": "t",
        "author": "a",
        "format": None,
        "date_completed": "d",
        "rating": None,
        "notes": None,
        "shelf": None,
    }
    rows = [
        {"t": "Libby Timestamp", "a": "X", "d": "October 14, 2017 0:34"},
        {"t": "Month Name Only", "a": "X", "d": "November 06, 2017"},
        {"t": "Abbrev Month", "a": "X", "d": "Oct 14, 2017"},
        {"t": "US Datetime", "a": "X", "d": "10/14/2017 16:54"},
        {"t": "ISO Datetime", "a": "X", "d": "2017-10-14 16:54:02"},
    ]
    parsed = parsing.parse_rows(rows, mapping)
    expected = [
        date(2017, 10, 14),
        date(2017, 11, 6),
        date(2017, 10, 14),
        date(2017, 10, 14),
        date(2017, 10, 14),
    ]
    for p, want in zip(parsed, expected, strict=True):
        assert p.date_completed == want, p.raw_title
        assert p.bad_date is False, p.raw_title


def test_parse_rows_flags_future_datetime_bearing_dates():
    mapping = {
        "title": "t",
        "author": "a",
        "format": None,
        "date_completed": "d",
        "rating": None,
        "notes": None,
        "shelf": None,
    }
    rows = [{"t": "Future Libby", "a": "X", "d": "October 14, 2999 0:34"}]
    parsed = parsing.parse_rows(rows, mapping)
    assert parsed[0].date_completed is None
    assert parsed[0].bad_date is True


def test_parse_rows_flags_future_and_unparseable_dates_and_defaults_format():
    mapping = {
        "title": "t",
        "author": "a",
        "format": None,
        "date_completed": "d",
        "rating": None,
        "notes": None,
        "shelf": None,
    }
    rows = [
        {"t": "Future Book", "a": "X", "d": "2999-01-01"},
        {"t": "Junk Date", "a": "Y", "d": "not a date"},
    ]
    parsed = parsing.parse_rows(rows, mapping)
    assert all(p.raw_format == "ebook" for p in parsed)  # unmapped format -> default
    assert all(p.date_completed is None and p.bad_date is True for p in parsed)
    assert all(p.shelf == "" for p in parsed)  # unmapped shelf -> ''
