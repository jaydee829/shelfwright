from unittest.mock import patch

import pytest
from agentic_librarian.scouts.metadata_scout import GoogleBooksScout


@pytest.fixture(autouse=True)
def mock_search_api_key(request):
    if "api_dependent" in request.keywords:
        yield
    else:
        with patch.dict("os.environ", {"GOOGLE_SEARCH_API_KEY": "mock_key"}):
            yield


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
    scout = GoogleBooksScout(api_key="key")
    actual = scout._extract_year(input_date)
    assert actual == expected_year
