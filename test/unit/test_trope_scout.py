from unittest.mock import MagicMock, patch

import pytest
from agentic_librarian.scouts.metadata_scout import LLMTropeScout


@pytest.fixture
def mock_genai_client():
    with patch("agentic_librarian.scouts.metadata_scout.genai.Client") as mock:
        client_inst = mock.return_value
        yield client_inst


def test_llm_trope_scout(mock_genai_client):
    scout = LLMTropeScout(api_key="fake-key")

    # Mock LLM response
    mock_response = MagicMock()
    mock_response.text = """
    {
        "tropes": [
            {
                "trope_name": "Found Family",
                "description": "A group of people who are not related by blood but form a deep familial bond.",
                "relevance_score": 0.9,
                "justification": "The crew of the Rocinante forms a tight-knit family unit throughout the series."
            }
        ]
    }
    """
    mock_genai_client.models.generate_content.return_value = mock_response

    res = scout.search("Leviathan Wakes", "James S.A. Corey")

    assert "tropes" in res
    assert len(res["tropes"]) == 1
    assert res["tropes"][0]["trope_name"] == "Found Family"
    assert res["tropes"][0]["relevance_score"] == 0.9
