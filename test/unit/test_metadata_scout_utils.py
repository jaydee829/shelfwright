import pytest

from src.agentic_librarian.scouts.metadata_scout import MultiSourceScout


@pytest.mark.parametrize(
    "input_date,expected_year",
    [
        ("2023-01-01", 2023),
        ("2023", 2023),
        ("January 2023", 2023),
        ("2023/05/01", 2023),
        ("Invalid", None),
        ("", None),
        (None, None),
        ("1954", 1954),
        ("Publication Year: 1999.", 1999),
    ],
)
def test_extract_year(input_date, expected_year):
    scout = MultiSourceScout()
    actual = scout._extract_year(input_date)
    assert actual == expected_year
