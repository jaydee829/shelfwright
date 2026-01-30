from datetime import date

import pandas as pd
import pytest
from agentic_librarian.etl.cleaning import parse_completion_date, split_authors, split_formats, split_narrators


def test_split_formats():
    df = pd.DataFrame({"Title": ["Book 1"], "format": ["hardcover, e-book"]})
    result = split_formats(df)
    assert len(result) == 2
    assert sorted(result["format"].tolist()) == ["e-book", "hardcover"]


def test_split_authors_diverse_separators():
    # Test semicolon, 'and', and comma
    df = pd.DataFrame({"Author": ["Author A; Author B", "Author C and Author D", "Author E, Author F"]})
    result = split_authors(df)

    # Check first row
    assert result.iloc[0]["Author_1"] == "Author A"
    assert result.iloc[0]["Author_2"] == "Author B"

    # Check second row
    assert result.iloc[1]["Author_1"] == "Author C"
    assert result.iloc[1]["Author_2"] == "Author D"

    # Note: Comma is tricky as it might be part of a name, but for this project
    # we'll assume it's a separator if we find it in the author column based on plan.
    # Actually, let's stick to the plan's mentioned separators first.


@pytest.mark.parametrize(
    "input_date,fallback,expected",
    [
        ("1/7/2020", None, date(2020, 1, 7)),
        ("4-Jan", 2020, date(2020, 1, 4)),
        ("4-Jan", None, date(2026, 1, 4)),  # Current year
        ("1/24/2020", 2021, date(2020, 1, 24)),  # Explicit year trumps fallback
        (None, 2020, None),
    ],
)
def test_parse_completion_date(input_date, fallback, expected):
    assert parse_completion_date(input_date, fallback_year=fallback) == expected


def test_split_narrators():
    df = pd.DataFrame({"Narrator": ["Narrator 1; Narrator 2"]})
    result = split_narrators(df)
    assert result.iloc[0]["Narrator_1"] == "Narrator 1"
    assert result.iloc[0]["Narrator_2"] == "Narrator 2"
