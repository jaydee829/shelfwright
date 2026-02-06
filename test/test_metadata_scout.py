import os
from unittest.mock import MagicMock

import pytest
import requests

import src.agentic_librarian.scouts.metadata_scout as md_scout


def test_fetch_book_metadata_success(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {
                "items": [
                    {
                        "id": "test_id",
                        "volumeInfo": {
                            "title": "Test Book",
                            "authors": ["Test Author"],
                            "publishedDate": "2020-01-01",
                            "description": "A test book description.",
                            "pageCount": 123,
                            "categories": ["Fiction"],
                            "averageRating": 4.5,
                            "imageLinks": {"thumbnail": "http://example.com/thumb.jpg"},
                        },
                    }
                ]
            }

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata("Test Book", "Test Author")
    assert metadata is not None
    assert metadata["google_id"] == "test_id"
    assert metadata["title"] == "Test Book"
    assert metadata["authors"] == ["Test Author"]
    assert metadata["published_date"] == "2020-01-01"
    assert metadata["description"] == "A test book description."
    assert metadata["page_count"] == 123
    assert metadata["genres"] == ["Fiction"]
    assert metadata["average_rating"] == 4.5
    assert metadata["thumbnail"] == "http://example.com/thumb.jpg"


def test_fetch_book_metadata_no_results(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {}

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata("Nonexistent Book", "Unknown Author")
    assert metadata is None

    def mock_get(*args, **kwargs):
        raise requests.exceptions.RequestException("API failure")

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata("Any Book", "Any Author")
    assert metadata is None


def test_fetch_book_metadata_missing_fields(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {
                "items": [
                    {
                        "id": "test_id_missing_fields",
                        "volumeInfo": {
                            "title": "Test Book Missing Fields",
                            # 'authors' field is missing
                            "publishedDate": "2021-05-05",
                        },
                    }
                ]
            }

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata("Test Book Missing Fields", "Test Author")
    assert metadata is not None
    assert metadata["google_id"] == "test_id_missing_fields"
    assert metadata["title"] == "Test Book Missing Fields"
    assert metadata["authors"] == []  # Default to empty list when missing
    assert metadata["published_date"] == "2021-05-05"


def test_fetch_book_metadata_no_items_key(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {"kind": "books#volumes"}

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.get", mock_get)

    metadata = md_scout.fetch_google_books_metadata("Some Book", "Some Author")
    assert metadata is None


@pytest.mark.integration
@pytest.mark.api_dependent
def test_fetch_book_metadata_integration_live():
    # Skip if explicitly disabled to avoid network calls in some CI environments
    if os.environ.get("SKIP_INTEGRATION_TESTS") == "1":
        pytest.skip("Skipping integration tests (SKIP_INTEGRATION_TESTS=1)")

    title = "The Way of Kings"
    author = "Brandon Sanderson"
    api_key = os.environ.get("GOOGLE_BOOKS_API_KEY")

    if api_key:
        metadata = md_scout.fetch_google_books_metadata(title, author, api_key=api_key)
    else:
        metadata = md_scout.fetch_google_books_metadata(title, author)

    assert metadata is not None, "Expected live API to return metadata"
    assert "google_id" in metadata
    assert "title" in metadata
    assert "authors" in metadata

    # Ensure the returned metadata matches the queried book/author (case-insensitive, tolerant)
    title_ok = "way of kings" in metadata["title"].lower()
    authors = metadata.get("authors") or []
    author_ok = any("sanderson" in a.lower() for a in authors)
    assert title_ok or author_ok, f"Returned metadata does not appear to match '{title}' by '{author}'"


@pytest.mark.integration
@pytest.mark.api_dependent
def test_fetch_audible_metadata_integration_live():
    # Skip if explicitly disabled to avoid network calls in some CI environments
    if os.environ.get("SKIP_INTEGRATION_TESTS") == "1":
        pytest.skip("Skipping integration tests (SKIP_INTEGRATION_TESTS=1)")

    title = "The Way of Kings"
    author = "Brandon Sanderson"

    metadata = md_scout.AudiobookScout().extract_metadata_with_gemini(title)

    assert metadata is not None, "Expected live API to return metadata"
    assert "title" in metadata
    assert "narrator" in metadata
    assert "length_minutes" in metadata

    # Ensure the returned metadata matches the queried book/author (case-insensitive, tolerant)
    title_ok = "way of kings" in metadata["title"].lower()
    author_ok = "sanderson" in metadata["narrator"].lower()
    assert title_ok or author_ok, f"Returned metadata does not appear to match '{title}' by '{author}'"


def test_fetch_hardcover_metadata_no_api_key():
    title = "The Way of Kings"
    author = "Brandon Sanderson"

    with pytest.raises(ValueError, match="Hardcover API key not set"):
        md_scout.fetch_hardcover_metadata(title, author, format="Audiobook", api_key=None)


def test_fetch_hardcover_metadata_api_failure(monkeypatch):
    def mock_get(*args, **kwargs):
        raise requests.exceptions.RequestException("API failure")

    monkeypatch.setattr("requests.get", mock_get)

    title = "The Way of Kings"
    author = "Brandon Sanderson"
    api_key = "dummy_api_key"

    metadata = md_scout.fetch_hardcover_metadata(title, author, format="Audiobook", api_key=api_key)
    assert metadata == {}, "Expected empty dict on API failure"


def test_fetch_hardcover_metadata_no_results(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {"editions": []}

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.get", mock_get)

    title = "Nonexistent Book"
    author = "Unknown Author"
    api_key = "dummy_api_key"

    metadata = md_scout.fetch_hardcover_metadata(title, author, format="Audiobook", api_key=api_key)
    assert metadata == {}, "Expected empty dict when no results found"


def test_fetch_hardcover_metadata_paperback_format(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {
                "data": {
                    "editions": [
                        {
                            "title": "Test Paperback Book",
                            "edition_format": "Paperback",
                            "pages": 350,
                            "isbn_13": "1234567890123",
                            "book": {
                                "description": "A test paperback book description.",
                                "contributions": [{"author": {"name": "Test Author"}}],
                            },
                        }
                    ]
                }
            }

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.post", mock_get)

    title = "Test Paperback Book"
    author = "Test Author"
    api_key = "dummy_api_key"

    metadata = md_scout.fetch_hardcover_metadata(title, author, format="Paperback", api_key=api_key)
    assert metadata is not None
    assert metadata["title"] == "Test Paperback Book"
    assert metadata["edition_format"] == "Paperback"
    assert metadata["page_count"] == 350
    assert metadata["description"] == "A test paperback book description."
    assert metadata["authors"] == ["Test Author"]
    assert metadata["publication_date"] is None  # Not in mock


def test_fetch_hardcover_metadata_audiobook_format(monkeypatch):
    class MockResponse:
        @staticmethod
        def json():
            return {
                "data": {
                    "editions": [
                        {
                            "title": "Test Audiobook",
                            "edition_format": "Audiobook",
                            "audio_seconds": 7200,
                            "isbn_13": "9876543210987",
                            "book": {
                                "description": "A test audiobook description.",
                                "contributions": [{"author": {"name": "Narrator Name"}}],
                            },
                        }
                    ]
                }
            }

        @staticmethod
        def raise_for_status():
            pass

    def mock_get(*args, **kwargs):
        return MockResponse()

    monkeypatch.setattr("requests.post", mock_get)

    title = "Test Audiobook"
    author = "Narrator Name"
    api_key = "dummy_api_key"

    metadata = md_scout.fetch_hardcover_metadata(title, author, format="Audiobook", api_key=api_key)
    assert metadata is not None
    assert metadata["title"] == "Test Audiobook"
    assert metadata["edition_format"] == "Audiobook"
    assert metadata["audio_minutes"] == 120  # 7200 seconds = 120 minutes
    assert metadata["description"] == "A test audiobook description."
    assert metadata["authors"] == ["Narrator Name"]


@pytest.mark.integration
@pytest.mark.api_dependent
def test_fetch_hardcover_metadata_integration_live():
    # Skip if explicitly disabled to avoid network calls in some CI environments
    if os.environ.get("SKIP_INTEGRATION_TESTS") == "1":
        pytest.skip("Skipping integration tests (SKIP_INTEGRATION_TESTS=1)")

    title = "The Way of Kings"
    format = "Audiobook"
    author = "Brandon Sanderson"
    api_key = os.environ.get("HARDCOVER_API_KEY")

    metadata = md_scout.fetch_hardcover_metadata(title, author, format=format, api_key=api_key)
    assert metadata is not None, "Expected live API to return metadata"
    assert "title" in metadata
    assert "moods" in metadata

    # Ensure the returned metadata matches the queried book/author (case-insensitive, tolerant)
    title_ok = "way of kings" in metadata["title"].lower()
    authors = metadata.get("author_names") or []
    author_ok = any("sanderson" in a.lower() for a in authors)
    assert title_ok or author_ok, f"Returned metadata does not appear to match '{title}' by '{author}'"


def test_multi_source_scout_merge_logic(monkeypatch):
    """Test that MultiSourceScout correctly merges data from Google and Hardcover."""

    def mock_google(title, author, api_key=None):
        return {
            "title": "Google Title",
            "authors": ["Google Author"],
            "ISBN_13": "1111111111111",
            "page_count": 100,
            "description": "Google Description",
            "genres": ["Fiction"],
            "average_rating": 4.0,
            "thumbnail": "http://google.com/thumb.jpg",
            "published_date": "2020-01-01",
        }

    def mock_hardcover(title, author, format, api_key=None):
        return {
            "title": "Hardcover Title",
            "authors": ["Hardcover Author"],
            "isbn_13": "2222222222222",
            "page_count": 200,
            "description": "Hardcover Description",
            "genres": {"Fantasy"},
            "moods": {"Adventurous"},
            "publication_date": "2021-01-01",
            "original_publication_date": "2021-01-01",
            "audio_minutes": 600,
        }

    monkeypatch.setattr(md_scout, "fetch_google_books_metadata", mock_google)
    monkeypatch.setattr(md_scout, "fetch_hardcover_metadata", mock_hardcover)

    scout = md_scout.MultiSourceScout(google_api_key="gkey", hardcover_api_key="hkey")
    result = scout.scout_metadata("Test Book", "Test Author")

    # Verify prioritisation
    assert result["title"] == "Hardcover Title"
    assert result["authors"] == ["Hardcover Author"]
    assert result["isbn_13"] == "2222222222222"
    assert result["page_count"] == 200
    assert result["description"] == "Hardcover Description"
    assert sorted(result["genres"]) == ["Fantasy", "Fiction"]
    assert result["moods"] == ["Adventurous"]
    assert result["average_rating"] == 4.0
    assert result["thumbnail"] == "http://google.com/thumb.jpg"
    assert result["publication_date"] == "2021-01-01"
    assert result["original_publication_year"] == 2021
    assert result["audio_minutes"] == 600


def test_multi_source_scout_fallback_logic(monkeypatch):
    """Test that MultiSourceScout falls back to Google when Hardcover is missing data."""

    def mock_google(title, author, api_key=None):
        return {
            "title": "Google Title",
            "authors": ["Google Author"],
            "ISBN_13": "1111111111111",
            "page_count": 100,
            "genres": ["Fiction"],
            "published_date": "2020-01-01",
        }

    def mock_hardcover(title, author, format, api_key=None):
        return {}  # No results from Hardcover

    monkeypatch.setattr(md_scout, "fetch_google_books_metadata", mock_google)
    monkeypatch.setattr(md_scout, "fetch_hardcover_metadata", mock_hardcover)

    scout = md_scout.MultiSourceScout()
    result = scout.scout_metadata("Test Book", "Test Author")

    assert result["title"] == "Google Title"
    assert result["authors"] == ["Google Author"]
    assert result["isbn_13"] == "1111111111111"
    assert result["page_count"] == 100
    assert result["genres"] == ["Fiction"]
    assert result["original_publication_year"] == 2020


def test_multi_source_scout_audiobook_dual_pathway(monkeypatch):
    """Test that MultiSourceScout uses both pathways for audiobooks and logs to MLFlow."""

    # Mock MLFlow
    mock_mlflow = MagicMock()
    monkeypatch.setattr(md_scout, "mlflow", mock_mlflow)

    def mock_google(title, author, api_key=None):
        return {}

    def mock_hardcover(title, author, format, api_key=None):
        return {"audio_minutes": 100}

    def mock_audible(self, title):
        return {"length_minutes": 120}

    def mock_direct(self, title, author):
        return {"audio_minutes": 115}

    monkeypatch.setattr(md_scout, "fetch_google_books_metadata", mock_google)
    monkeypatch.setattr(md_scout, "fetch_hardcover_metadata", mock_hardcover)
    monkeypatch.setattr(md_scout.AudiobookScout, "extract_metadata_with_gemini", mock_audible)
    monkeypatch.setattr(md_scout.DirectKnowledgeScout, "scout_audiobook", mock_direct)

    scout = md_scout.MultiSourceScout()
    result = scout.scout_metadata("Test Book", "Test Author", format="Audiobook")

    # Verify selection logic (Prefer Pathway A / Audible)
    assert result["audio_minutes"] == 120

    # Verify MLFlow calls
    assert mock_mlflow.set_experiment.called
    assert mock_mlflow.start_run.called
    # Check that metrics were logged
    log_metric_calls = [call[0] for call in mock_mlflow.log_metric.call_args_list]
    metric_names = [call[0] for call in log_metric_calls]
    assert "pathway_a_minutes" in metric_names
    assert "pathway_b_minutes" in metric_names
