import os

import pytest

import agentic_librarian.scouts.metadata_scout as md_scout


@pytest.mark.integration
@pytest.mark.api_dependent
def test_fetch_book_metadata_integration_live():
    if os.environ.get("SKIP_INTEGRATION_TESTS") == "1":
        pytest.skip("Skipping integration tests")

    scout = md_scout.GoogleBooksScout()
    metadata = scout.search("The Way of Kings", "Brandon Sanderson")
    assert "title" in metadata
    assert "isbn_13" in metadata
