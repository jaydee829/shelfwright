import os
from unittest.mock import patch

import agentic_librarian.scouts.metadata_scout as md_scout
import pytest


@pytest.fixture(autouse=True)
def mock_search_api_key(request):
    """Mock the search API key ONLY for non-live tests."""
    if "api_dependent" in request.keywords:
        yield
    else:
        with patch.dict("os.environ", {"GOOGLE_SEARCH_API_KEY": "mock_key"}):
            yield


@pytest.mark.parametrize(
    "mock_json,expected_title,expected_authors",
    [
        (
            {
                "items": [
                    {
                        "id": "test_id",
                        "volumeInfo": {
                            "title": "Test Book",
                            "authors": ["Test Author"],
                            "publishedDate": "2020-01-01",
                            "categories": ["Fiction"],
                        },
                    }
                ]
            },
            "Test Book",
            ["Test Author"],
        ),
    ],
)
def test_google_books_scout_search(monkeypatch, mock_json, expected_title, expected_authors):
    scout = md_scout.GoogleBooksScout(api_key="key")
    monkeypatch.setattr(scout, "_make_request", lambda *a, **k: mock_json)

    metadata = scout.search("Title", "Author")
    assert metadata["title"] == expected_title
    assert metadata["authors"] == expected_authors


@pytest.mark.parametrize(
    "mock_data,expected_pages",
    [
        (
            {
                "data": {
                    "editions": [
                        {
                            "title": "Test Hardcover",
                            "edition_format": "Hardcover",
                            "pages": 500,
                            "book": {"contributions": [{"author": {"name": "Auth"}}]},
                        }
                    ]
                }
            },
            500,
        ),
    ],
)
def test_hardcover_scout_search(monkeypatch, mock_data, expected_pages):
    scout = md_scout.HardcoverScout(api_key="key")
    monkeypatch.setattr(scout, "_make_request", lambda *a, **k: mock_data)

    metadata = scout.search("Title", "Author", format="Hardcover")
    assert metadata["page_count"] == expected_pages


def test_scout_manager_merging():
    """Verify that ScoutManager correctly merges and prioritizes data from multiple scouts."""
    manager = md_scout.ScoutManager()

    class FakeScout(md_scout.BaseScout):
        def __init__(self, data):
            super().__init__()
            self.data = data

        def search(self, t, a, **k):
            return self.data

    scout1 = FakeScout({"title": "Priority 1", "page_count": 100})
    scout2 = FakeScout({"title": "Priority 2", "description": "Desc 2"})

    manager.register_scout(scout1, priority=1)
    manager.register_scout(scout2, priority=2)

    result = manager.enrich("Original", "Author")

    # 1. Priority 1 wins for title
    assert result["title"] == "Priority 1"
    # 2. Both fields captured
    assert result["page_count"] == 100
    assert result["description"] == "Desc 2"
    # 3. Source tracking
    assert "FakeScout" in result["source_priority"]


@pytest.mark.integration
@pytest.mark.api_dependent
def test_fetch_book_metadata_integration_live():
    if os.environ.get("SKIP_INTEGRATION_TESTS") == "1":
        pytest.skip("Skipping integration tests")

    scout = md_scout.GoogleBooksScout()
    metadata = scout.search("The Way of Kings", "Brandon Sanderson")
    assert "title" in metadata
    assert "isbn_13" in metadata
