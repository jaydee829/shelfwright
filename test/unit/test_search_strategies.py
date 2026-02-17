import os
from unittest.mock import MagicMock, patch

import pytest
from agentic_librarian.agents.search_strategies import ExternalA2AAgent, InternalSearchAgent, run_search_experiment


@pytest.fixture
def mock_genai_client():
    with patch("agentic_librarian.agents.search_strategies.genai.Client") as mock:
        client = mock.return_value
        response = MagicMock()
        response.text = "[]"
        client.models.generate_content.return_value = response
        yield client


@pytest.fixture
def mock_mlflow():
    with patch("agentic_librarian.agents.search_strategies.mlflow") as mock:
        yield mock


def test_internal_search_agent_logging(mock_genai_client, mock_mlflow):
    mock_genai_client.models.generate_content.return_value.text = '[{"title": "Book A"}]'

    with patch.dict(os.environ, {"GOOGLE_SEARCH_API_KEY": "fake"}):
        agent = InternalSearchAgent("test_internal")
        results = agent.search("scifi")

        assert len(results) == 1
        assert results[0]["title"] == "Book A"
        assert mock_mlflow.log_metric.called


def test_external_a2a_agent_sim(mock_genai_client, mock_mlflow):
    mock_genai_client.models.generate_content.return_value.text = '```json\n[{"title": "A2A Book"}]\n```'

    with patch.dict(os.environ, {"GOOGLE_SEARCH_API_KEY": "fake"}):
        agent = ExternalA2AAgent("test_a2a")
        results = agent.search("fantasy")

        assert len(results) == 1
        assert results[0]["title"] == "A2A Book"
        assert mock_mlflow.log_metric.called


def test_run_search_experiment(mock_genai_client, mock_mlflow):
    with patch.dict(os.environ, {"GOOGLE_SEARCH_API_KEY": "fake"}):
        results = run_search_experiment("detective books")

        assert "internal" in results
        assert "external" in results
        assert mock_mlflow.set_experiment.called
        assert mock_mlflow.start_run.called
