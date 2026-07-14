from unittest.mock import MagicMock, patch

import pytest

from agentic_librarian.db.models import Trope
from agentic_librarian.scouts import utils
from agentic_librarian.scouts.trope_manager import TropeManager


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture
def mock_genai_client(monkeypatch):
    # Mock the shared genai client in utils to avoid actual network calls
    utils.get_cached_embedding.cache_clear()
    mock_client = MagicMock()
    monkeypatch.setattr("agentic_librarian.scouts.utils._shared_client", mock_client)
    yield mock_client
    utils.get_cached_embedding.cache_clear()


@pytest.fixture
def trope_manager(mock_session, mock_genai_client):
    return TropeManager(session=mock_session, api_key="fake_key")


@pytest.mark.parametrize(
    "existing_tropes,target_embedding,threshold,expected_found",
    [
        # Case 1: Found
        (
            [Trope(name="Existing", embedding=[0.9, 0.1, 0.0])],
            [0.89, 0.11, 0.01],
            0.9,
            True,
        ),
        # Case 2: Not found
        (
            [Trope(name="Different", embedding=[0.0, 1.0, 0.0])],
            [1.0, 0.0, 0.0],
            0.9,
            False,
        ),
        # Case 3: Skip None embeddings
        (
            [Trope(name="No Embed", embedding=None), Trope(name="With", embedding=[1.0, 0.0, 0.0])],
            [0.99, 0.0, 0.0],
            0.9,
            True,
        ),
    ],
)
def test_find_similar_trope_parameterized(trope_manager, existing_tropes, target_embedding, threshold, expected_found):
    # Setup mock chain for find_similar_trope
    mock_query = trope_manager.session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query

    if expected_found:
        # Return the "found" one (the last one with valid embedding in Case 3)
        found_trope = [t for t in existing_tropes if t.embedding is not None][0]
        mock_query.first.return_value = found_trope
    else:
        mock_query.first.return_value = None

    similar = trope_manager.find_similar_trope(target_embedding, threshold=threshold)
    if expected_found:
        assert similar is not None
        assert similar.name in [t.name for t in existing_tropes]
    else:
        assert similar is None


def test_standardize_trope_new(trope_manager, mock_genai_client):
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.5] * 1536)]
    mock_genai_client.models.embed_content.return_value = mock_response

    # Setup mock chain for standardize_trope
    mock_query = trope_manager.session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.order_by.return_value = mock_query
    # Return None for both exact name match check and semantic match check
    mock_query.first.side_effect = [None, None]

    standardized = trope_manager.standardize_trope("Shiny New Trope")
    assert standardized.name == "Shiny New Trope"
    trope_manager.session.add.assert_called_once()


def test_trope_manager_missing_api_key():
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match="Google API key not set for TropeManager."),
    ):
        TropeManager(session=MagicMock())


@pytest.mark.parametrize(
    "existing_tropes,expected_name",
    [
        # Case 1: exact-name hit returns the existing trope, no new row created.
        ([Trope(name="Dark", embedding=[0.1, 0.2, 0.3])], "Dark"),
    ],
)
def test_get_or_create_fallback_trope_exact_hit(trope_manager, existing_tropes, expected_name):
    mock_query = trope_manager.session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = existing_tropes[0]

    result = trope_manager.get_or_create_fallback_trope("Dark")

    assert result is existing_tropes[0]
    assert result.name == expected_name
    trope_manager.session.add.assert_not_called()


def test_get_or_create_fallback_trope_miss_never_semantically_redirects(trope_manager, mock_genai_client):
    """The anti-#70 assertion: even when a semantically-near trope exists (mocked here to
    guarantee a would-be >=0.85 cosine match via find_similar_trope), get_or_create_fallback_trope
    must NEVER return it — only an exact-name match may short-circuit creation."""
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.5] * 1536)]
    mock_genai_client.models.embed_content.return_value = mock_response

    near_trope = Trope(name="The Dark Night of the Soul", embedding=[0.5] * 1536)

    # Exact-name lookup misses.
    mock_query = trope_manager.session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = None

    # Even if find_similar_trope WOULD find a near match, get_or_create_fallback_trope must
    # never call it — patch it to prove it's not consulted at all.
    with patch.object(trope_manager, "find_similar_trope", return_value=near_trope) as mock_find_similar:
        result = trope_manager.get_or_create_fallback_trope("Dark")

    mock_find_similar.assert_not_called()
    assert result is not near_trope
    assert result.name == "Dark"
    trope_manager.session.add.assert_called_once()
    trope_manager.session.flush.assert_called_once()


def test_get_or_create_fallback_trope_does_not_update_description(trope_manager):
    """Unlike standardize_trope, the exact-name hit path never touches description (brief:
    'do NOT update description')."""
    existing = Trope(name="Cozy", embedding=[0.1, 0.2, 0.3], description=None)
    mock_query = trope_manager.session.query.return_value
    mock_query.filter.return_value = mock_query
    mock_query.first.return_value = existing

    result = trope_manager.get_or_create_fallback_trope("Cozy")

    assert result is existing
    assert existing.description is None
