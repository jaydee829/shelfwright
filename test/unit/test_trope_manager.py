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
    trope_manager.session.query.return_value.all.return_value = existing_tropes
    similar = trope_manager.find_similar_trope(target_embedding, threshold=threshold)
    if expected_found:
        assert similar is not None
        # Check if it matches one of the provided ones
        assert similar.name in [t.name for t in existing_tropes]
    else:
        assert similar is None


def test_standardize_trope_new(trope_manager, mock_genai_client):
    mock_response = MagicMock()
    mock_response.embeddings = [MagicMock(values=[0.5] * 1536)]
    mock_genai_client.models.embed_content.return_value = mock_response

    trope_manager.session.query.return_value.filter.return_value.first.return_value = None
    trope_manager.session.query.return_value.all.return_value = []

    standardized = trope_manager.standardize_trope("Shiny New Trope")
    assert standardized.name == "Shiny New Trope"
    trope_manager.session.add.assert_called_once()


def test_trope_manager_missing_api_key():
    with (
        patch.dict("os.environ", {}, clear=True),
        pytest.raises(ValueError, match="Google API key not set for TropeManager."),
    ):
        TropeManager(session=MagicMock())
