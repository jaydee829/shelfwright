from unittest.mock import MagicMock, patch

import pytest
from agentic_librarian.scouts.metadata_scout import StyleScout


@pytest.fixture
def mock_genai_client():
    with patch("agentic_librarian.scouts.metadata_scout.genai.Client") as mock:
        client_inst = mock.return_value
        yield client_inst


def test_scout_author_style(mock_genai_client):
    scout = StyleScout(api_key="fake-key")

    # Mock LLM response
    mock_response = MagicMock()
    mock_response.text = '{"pacing": "fast", "tone": "cynical", "style": "minimalist"}'
    mock_genai_client.models.generate_content.return_value = mock_response

    style = scout.scout_author_style("Ernest Hemingway")

    assert style["pacing"] == "fast"
    assert style["tone"] == "cynical"
    assert style["style"] == "minimalist"


def test_scout_narrator_style(mock_genai_client):
    scout = StyleScout(api_key="fake-key")

    # Mock LLM response
    mock_response = MagicMock()
    mock_response.text = '{"pacing": "steady", "voice_differentiation": "excellent", "emotional_range": "wide"}'
    mock_genai_client.models.generate_content.return_value = mock_response

    style = scout.scout_narrator_style("Jefferson Mays")

    assert style["pacing"] == "steady"
    assert style["voice_differentiation"] == "excellent"
    assert style["emotional_range"] == "wide"


def test_style_scout_search_mode(mock_genai_client):
    scout = StyleScout(api_key="fake-key")

    # Mock both calls
    mock_response = MagicMock()
    mock_response.text = '{"pacing": "fast"}'
    mock_genai_client.models.generate_content.return_value = mock_response

    res = scout.search("The Expanse", "James S.A. Corey", narrators=["Jefferson Mays"])

    assert "author_style" in res
    assert "narrator_styles" in res
    assert "Jefferson Mays" in res["narrator_styles"]
    assert res["author_style"]["pacing"] == "fast"
