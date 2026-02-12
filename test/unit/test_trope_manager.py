from unittest.mock import MagicMock, patch

import pytest
from agentic_librarian.db.models import Trope
from agentic_librarian.scouts.trope_manager import TropeManager


@pytest.fixture
def mock_session():
    return MagicMock()


@pytest.fixture
def mock_genai_client():
    with patch("agentic_librarian.scouts.trope_manager.genai.Client") as mock:
        yield mock.return_value


@pytest.fixture
def trope_manager(mock_session, mock_genai_client):
    return TropeManager(session=mock_session, api_key="fake_key")


def test_trope_manager_initialization(trope_manager):
    assert trope_manager.session is not None
    assert trope_manager.client is not None


def test_get_embedding(trope_manager, mock_genai_client):
    # Mock the embedding response
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.1, 0.2, 0.3])]
    mock_genai_client.models.embed_content.return_value = mock_response

    embedding = trope_manager._get_embedding("test trope")

    assert embedding == [0.1, 0.2, 0.3]
    mock_genai_client.models.embed_content.assert_called_once()


def test_find_similar_trope_found(trope_manager):
    # Mock existing tropes in DB
    existing_trope = Trope(name="Existing Trope", embedding=[0.9, 0.1, 0.0])
    trope_manager.session.query.return_value.all.return_value = [existing_trope]

    # Target embedding very similar to existing
    target_embedding = [0.89, 0.11, 0.01]

    similar = trope_manager.find_similar_trope(target_embedding, threshold=0.9)

    assert similar == existing_trope


def test_find_similar_trope_not_found(trope_manager):
    existing_trope = Trope(name="Different Trope", embedding=[0.0, 1.0, 0.0])
    trope_manager.session.query.return_value.all.return_value = [existing_trope]

    target_embedding = [1.0, 0.0, 0.0]

    similar = trope_manager.find_similar_trope(target_embedding, threshold=0.9)

    assert similar is None


def test_standardize_trope_new(trope_manager, mock_genai_client):
    # Mock embedding
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.5] * 1536)]
    mock_genai_client.models.embed_content.return_value = mock_response

    # Mock similar not found
    trope_manager.session.query.return_value.filter.return_value.first.return_value = None
    trope_manager.session.query.return_value.all.return_value = []

    standardized = trope_manager.standardize_trope("Shiny New Trope")

    assert standardized.name == "Shiny New Trope"
    assert standardized.embedding == [0.5] * 1536
    trope_manager.session.add.assert_called_once()


def test_standardize_trope_existing_exact(trope_manager):
    # Mock exact name match
    existing = Trope(name="Enemies to Lovers")
    trope_manager.session.query.return_value.filter.return_value.first.return_value = existing

    standardized = trope_manager.standardize_trope("Enemies to Lovers")

    assert standardized == existing
    trope_manager.session.add.assert_not_called()


def test_standardize_trope_existing_similar(trope_manager, mock_genai_client):
    # Mock embedding
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.1, 0.2])]
    mock_genai_client.models.embed_content.return_value = mock_response

    # Mock no exact match
    trope_manager.session.query.return_value.filter.return_value.first.return_value = None

    # Mock similar match
    similar = Trope(name="Similar Trope", embedding=[0.11, 0.19])
    trope_manager.session.query.return_value.all.return_value = [similar]

    standardized = trope_manager.standardize_trope("Almost Similar Trope")

    assert standardized == similar


def test_trope_manager_missing_api_key():
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match="Google API key not set for TropeManager."),
    ):
        TropeManager(session=MagicMock())


def test_find_similar_trope_skips_none_embedding(trope_manager):
    # Mock tropes in DB, one with None embedding
    trope_no_embed = Trope(name="No Embed", embedding=None)
    trope_with_embed = Trope(name="With Embed", embedding=[0.0, 1.0, 0.0])
    trope_manager.session.query.return_value.all.return_value = [trope_no_embed, trope_with_embed]

    target_embedding = [0.0, 0.9, 0.1]

    similar = trope_manager.find_similar_trope(target_embedding, threshold=0.9)

    assert similar == trope_with_embed
