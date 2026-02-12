from unittest.mock import MagicMock, patch

import pytest
from agentic_librarian.db.session import DatabaseManager
from agentic_librarian.orchestration.definitions import defs
from dagster import DagsterInstance


@pytest.fixture
def mock_db_manager():
    manager = MagicMock(spec=DatabaseManager)
    session = MagicMock()
    manager.get_session.return_value.__enter__.return_value = session
    return manager


@pytest.fixture
def mock_scout():
    with patch("agentic_librarian.orchestration.assets.MultiSourceScout") as mock:
        scout_inst = mock.return_value
        scout_inst.scout_metadata.return_value = {
            "title": "Mock Title",
            "authors": ["Mock Author"],
            "isbn_13": "1234567890123",
            "genres": ["Fantasy"],
            "moods": ["Epic"],
        }
        yield scout_inst


@pytest.fixture
def mock_trope_manager():
    with patch("agentic_librarian.orchestration.assets.TropeManager") as mock:
        tm_inst = mock.return_value
        tm_inst.standardize_trope.return_value = MagicMock(id="mock-trope-id", name="Fantasy")
        yield tm_inst


...


@pytest.fixture
def mock_mlflow():
    with patch("agentic_librarian.orchestration.assets.mlflow") as mock:
        yield mock


def test_enhance_job_integration(mock_db_manager, mock_scout, mock_trope_manager, mock_mlflow):
    # Resolve the job from definitions
    job_def = defs.get_job_def("enhance_job")

    # Need an instance to handle dynamic partitions
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions("csv_files", ["test_sample"])

    # Execute the job in-process
    result = job_def.execute_in_process(
        partition_key="test_sample", resources={"db_manager": mock_db_manager}, instance=instance
    )

    assert result.success

    # Verify assets were executed
    # raw_history -> enriched_metadata -> vectorized_tropes
    assert result.output_for_node("raw_history") is not None
    assert result.output_for_node("enriched_metadata") is not None

    # Verify DB manager was used
    mock_db_manager.get_session.assert_called()

    # Verify scout was called
    assert mock_scout.scout_metadata.call_count == 2  # Two rows in test_sample.csv
