from datetime import date

import pandas as pd
import pytest
from agentic_librarian.etl.cleaning import parse_completion_date, split_authors, split_formats, split_narrators


@pytest.mark.parametrize(
    "input_fmt,expected",
    [
        ("Hardcover", ["Hardcover"]),
        ("Hardcover, eBook", ["Hardcover", "eBook"]),
        ("Paperback, Audiobook", ["Paperback", "Audiobook"]),
        ("  hardcover ,  e-book  ", ["hardcover", "e-book"]),
    ],
)
def test_split_formats_parameterized(input_fmt, expected):
    df = pd.DataFrame({"format": [input_fmt]})
    result = split_formats(df)
    assert sorted(result["format"].tolist()) == sorted(expected)


def test_split_formats_no_format_column():
    df = pd.DataFrame({"title": ["Book A"]})
    with pytest.raises(ValueError, match="DataFrame must contain a 'format' column"):
        split_formats(df)


@pytest.mark.parametrize(
    "input_author,expected_list",
    [
        ("Author One", ["Author One"]),
        ("Author A; Author B", ["Author A", "Author B"]),
        ("Author C and Author D", ["Author C", "Author D"]),
        ("Author E & Author F", ["Author E", "Author F"]),
        ("Sanderson, Brandon", ["Sanderson, Brandon"]),
        ("Sanderson, Brandon; Jordan, Robert", ["Sanderson, Brandon", "Jordan, Robert"]),
        ("Robert Jordan, Brandon Sanderson", ["Robert Jordan", "Brandon Sanderson"]),
        ("Kurt Vonnegut, Jr.", ["Kurt Vonnegut, Jr."]),
        ("Martin, George R. R.", ["Martin, George R. R."]),
    ],
)
def test_split_authors_parameterized(input_author, expected_list):
    df = pd.DataFrame({"Author": [input_author]})
    result = split_authors(df)

    # Collect all Author_X values that aren't None/NaN
    actual = []
    for i in range(1, len(expected_list) + 1):
        col = f"Author_{i}"
        val = result.iloc[0][col]
        if val and not pd.isna(val):
            actual.append(val)

    assert actual == expected_list


def test_split_authors_multiple_authors_batch():
    """Verify batched processing and column indexing for multiple rows."""
    df = pd.DataFrame(
        {
            "Author": [
                "Author One; Author Two",
                "Author Three; Author Four; Author Five",
            ]
        }
    )
    result = split_authors(df)
    assert "Author_1" in result.columns
    assert "Author_2" in result.columns
    assert "Author_3" in result.columns

    assert result.iloc[0]["Author_1"] == "Author One"
    assert result.iloc[0]["Author_2"] == "Author Two"
    assert pd.isna(result.iloc[0].get("Author_3"))

    assert result.iloc[1]["Author_1"] == "Author Three"
    assert result.iloc[1]["Author_2"] == "Author Four"
    assert result.iloc[1]["Author_3"] == "Author Five"


@pytest.mark.parametrize(
    "input_date,fallback,expected",
    [
        ("1/7/2020", None, date(2020, 1, 7)),
        ("4-Jan", 2020, date(2020, 1, 4)),
        ("4-Jan", None, date(2026, 1, 4)),  # Current year (2026 in system context)
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


def test_split_authors_after_multiformat_explode():
    # Regression: split_formats explodes multi-format rows, duplicating index labels. split_authors
    # then concats author columns with axis=1, which raised InvalidIndexError on the non-unique index.
    # split_formats now resets the index so the column-wise concat aligns.
    df = pd.DataFrame(
        {
            "Title": ["Multi", "Single"],
            "Author": ["Solo Author", "First Author and Second Author"],
            "format": ["hardcover, audiobook", "ebook"],
        }
    )
    result = split_authors(split_formats(df))
    assert result.index.is_unique
    assert len(result) == 3  # the multi-format row exploded into two
    assert sorted(result["format"].tolist()) == ["audiobook", "ebook", "hardcover"]
    single = result[result["Title"] == "Single"].iloc[0]
    assert single["Author_1"] == "First Author" and single["Author_2"] == "Second Author"
