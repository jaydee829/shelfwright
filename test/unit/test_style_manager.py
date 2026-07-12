from unittest.mock import MagicMock

import pytest

from agentic_librarian.db.models import Style
from agentic_librarian.scouts import utils
from agentic_librarian.scouts.style_manager import StyleManager


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture
def mock_genai_client(monkeypatch):
    # Mock the shared genai client in utils to avoid actual network calls
    utils.get_cached_embedding.cache_clear()
    mock_client = MagicMock()
    # Default embedding return
    mock_embedding = MagicMock()
    mock_embedding.values = [0.1] * 1536
    mock_client.models.embed_content.return_value.embeddings = [mock_embedding]
    monkeypatch.setattr("agentic_librarian.scouts.utils._shared_client", mock_client)
    yield mock_client
    utils.get_cached_embedding.cache_clear()


def test_standardize_style_exact_match(mock_session, mock_genai_client):
    manager = StyleManager(session=mock_session, api_key="fake-key")

    # Mock exact match
    existing_style = Style(name="fast-paced", category="Author")
    mock_session.query.return_value.filter.return_value.first.return_value = existing_style

    result = manager.standardize_style("fast-paced", category="Author")

    assert result == existing_style
    # Ensure no embedding was fetched since exact match found
    mock_genai_client.models.embed_content.assert_not_called()


def test_standardize_style_semantic_match(mock_session, mock_genai_client):
    manager = StyleManager(session=mock_session, api_key="fake-key")

    # 1. No exact match (first call to first())
    # 2. Similar match exists (second call to first() through find_similar_style)
    similar_style = Style(name="brisk", category="Author", embedding=[0.1] * 1536)

    # Configure the chain for find_similar_style
    mock_query = mock_session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.first.side_effect = [None, similar_style]

    result = manager.standardize_style("fast", category="Author")

    assert result == similar_style
    mock_genai_client.models.embed_content.assert_called_once()


def test_standardize_style_create_new(mock_session, mock_genai_client):
    manager = StyleManager(session=mock_session, api_key="fake-key")

    # Configure the chain to return None for both exact and similar match
    mock_query = mock_session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    mock_query.first.return_value = None

    result = manager.standardize_style("unique-style", category="Narrator")

    assert result.name == "unique-style"
    assert result.category == "Narrator"
    mock_session.add.assert_called_once()
    mock_session.flush.assert_called_once()
