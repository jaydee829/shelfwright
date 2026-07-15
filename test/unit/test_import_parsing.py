from datetime import date

import pytest

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


_DATE_ONLY_MAPPING = {
    "title": "t",
    "author": "a",
    "format": None,
    "date_completed": "d",
    "rating": None,
    "notes": None,
    "shelf": None,
}


def _parse_one_date(raw: str) -> parsing.ParsedRow:
    return parsing.parse_rows([{"t": "T", "a": "A", "d": raw}], _DATE_ONLY_MAPPING)[0]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # Numeric formats (Goodreads, ISO, US — day-first ambiguity resolved as US).
        ("2024/03/05", date(2024, 3, 5)),
        ("2024-03-05", date(2024, 3, 5)),
        ("3/5/2024", date(2024, 3, 5)),  # non-zero-padded
        ("10/14/2017 16:54", date(2017, 10, 14)),
        ("10/14/17", date(2017, 10, 14)),  # Excel two-digit year
        ("10-14-2017", date(2017, 10, 14)),
        ("10/14/2017 4:54 PM", date(2017, 10, 14)),  # Excel/Sheets 12-hour
        ("10/14/2017 4:54:02 PM", date(2017, 10, 14)),
        # Month-name formats. Libby's "timestamp" is "October 14, 2017 0:34" (naive local
        # time, non-zero-padded hour); only the calendar date matters (user report 2026-07-14).
        ("October 14, 2017 0:34", date(2017, 10, 14)),
        ("November 06, 2017", date(2017, 11, 6)),
        ("Oct 14, 2017 16:54", date(2017, 10, 14)),
        ("Oct 14, 2017", date(2017, 10, 14)),
        ("October 14 2017", date(2017, 10, 14)),  # comma stripped by a spreadsheet
        ("Oct 14 2017", date(2017, 10, 14)),
        ("14 October 2017", date(2017, 10, 14)),  # written-out European (month name is unambiguous)
        ("14 Oct 2017", date(2017, 10, 14)),
        ("14-Oct-2017", date(2017, 10, 14)),  # Excel DD-MMM-YYYY
        # Machine ISO-8601 timestamps.
        ("2017-10-14 16:54:02", date(2017, 10, 14)),
        ("2017-10-14 16:54", date(2017, 10, 14)),
        ("2017-10-14T16:54:02", date(2017, 10, 14)),
        ("2017-10-14T16:54:02Z", date(2017, 10, 14)),
        ("2017-10-14T16:54:02.123+02:00", date(2017, 10, 14)),
        ("2017-10-14 16:54:02.123456", date(2017, 10, 14)),
    ],
)
def test_parse_rows_accepts_known_date_formats(raw, expected):
    p = _parse_one_date(raw)
    assert p.date_completed == expected
    assert p.bad_date is False


@pytest.mark.parametrize(
    "raw",
    [
        "not a date",
        "2999-01-01",  # future
        "October 14, 2999 0:34",  # future, datetime-bearing
        "14 days",  # Libby "details" column — a mis-mapping must be loud
        "5",  # a rating cell; dateutil would fill in today's year+month (why we don't use it)
        "2017",  # bare year — must not resolve to an invented month/day
        "20171014",  # undelimited digits — reject rather than guess
        "9781524722166",  # ISBN
    ],
)
def test_parse_rows_flags_unusable_dates(raw):
    p = _parse_one_date(raw)
    assert p.date_completed is None
    assert p.bad_date is True


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


# --- _primary_author (#142): comma-joined/multi-author cells collapse to the primary
# author at parse time so raw_author is clean before work get-or-create matching. ---
@pytest.mark.parametrize(
    ("raw", "expected", "case_id"),
    [
        ("", "", "empty_string"),
        ("   ", "", "whitespace_only"),
        ("Frank Herbert", "Frank Herbert", "no_separator_returned_trimmed"),
        ("  Frank Herbert  ", "Frank Herbert", "no_separator_trims_surrounding_whitespace"),
        (
            "Casualfarmer, CasualFarmer",
            "Casualfarmer",
            "case_insensitive_duplicate_segments_take_first",
        ),
        (
            "  Casualfarmer ,  CasualFarmer  ",
            "Casualfarmer",
            "duplicate_segments_with_whitespace_around_separator",
        ),
        ("A, B, C", "A", "two_plus_commas_take_first_segment"),
        ("Author A, Author B, Author A", "Author A", "three_commas_take_first_segment"),
        ("Jane Doe and John Smith", "Jane Doe", "explicit_and_separator_takes_first"),
        ("Jane Doe & John Smith", "Jane Doe", "explicit_ampersand_separator_takes_first"),
        (
            "Jane Doe, John Smith",
            "Jane Doe",
            "single_comma_both_segments_multiword_takes_first",
        ),
        ("Ware, Ruth", "Ware, Ruth", "single_comma_last_first_kept_unchanged"),
        (
            "Le Guin, Ursula K.",
            "Le Guin, Ursula K.",
            "single_comma_last_first_with_initial_kept_unchanged",
        ),
        (", ", "", "degenerate_only_separator_yields_empty"),
        (" and ", "", "degenerate_only_and_separator_yields_empty"),
        (" & ", "", "degenerate_only_ampersand_separator_yields_empty"),
    ],
    ids=lambda param: param if isinstance(param, str) and param.isidentifier() else None,
)
def test_primary_author_decision_table(raw, expected, case_id):
    assert parsing._primary_author(raw) == expected


def test_parse_rows_normalizes_malformed_author_cell():
    mapping = {
        "title": "t",
        "author": "a",
        "format": None,
        "date_completed": None,
        "rating": None,
        "notes": None,
        "shelf": None,
    }
    rows = [{"t": "Beware of Chicken", "a": "Casualfarmer, CasualFarmer"}]
    parsed = parsing.parse_rows(rows, mapping)
    assert parsed[0].raw_author == "Casualfarmer"
