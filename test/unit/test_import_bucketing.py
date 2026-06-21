from datetime import date

from agentic_librarian.imports.bucketing import bucket
from agentic_librarian.imports.parsing import ParsedRow


def _row(shelf="read", d=date(2024, 1, 1), bad=False):
    return ParsedRow(
        raw_title="t",
        raw_author="a",
        raw_format="ebook",
        raw_date="x",
        date_completed=d,
        bad_date=bad,
        rating=None,
        notes=None,
        shelf=shelf,
    )


def test_read_with_date_goes_to_history():
    assert bucket(_row(), import_to_read=True, import_currently_reading=True) == ("history", None)


def test_generic_no_shelf_treated_as_read():
    assert bucket(_row(shelf=""), import_to_read=False, import_currently_reading=False) == ("history", None)


def test_read_without_date_is_skipped():
    assert bucket(_row(d=None), import_to_read=True, import_currently_reading=True) == ("skip", "no_completion_date")


def test_read_with_bad_date_is_skipped():
    assert bucket(_row(d=None, bad=True), import_to_read=True, import_currently_reading=True) == ("skip", "bad_date")


def test_to_read_routes_by_opt_in():
    assert bucket(_row(shelf="to-read", d=None), import_to_read=True, import_currently_reading=True) == (
        "suggestion",
        None,
    )
    assert bucket(_row(shelf="to-read", d=None), import_to_read=False, import_currently_reading=True) == (
        "skip",
        "to_read_opt_out",
    )


def test_currently_reading_routes_by_opt_in():
    assert bucket(_row(shelf="currently-reading", d=None), import_to_read=True, import_currently_reading=True) == (
        "suggestion",
        None,
    )
    assert bucket(_row(shelf="currently-reading", d=None), import_to_read=True, import_currently_reading=False) == (
        "skip",
        "currently_reading_opt_out",
    )
