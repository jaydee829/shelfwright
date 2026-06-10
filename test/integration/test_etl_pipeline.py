import os
from unittest.mock import MagicMock, patch

import pytest

# Mock credentials before importing defs
with patch.dict(
    os.environ,
    {"POSTGRES_USER": "mock_user", "POSTGRES_PASSWORD": "mock_password", "GOOGLE_SEARCH_API_KEY": "mock_key"},
):
    from dagster import DagsterInstance

    from agentic_librarian.db.models import Edition, ReadingHistory, Work
    from agentic_librarian.db.session import DatabaseManager
    from agentic_librarian.orchestration.definitions import defs


def create_mock_session(existing_edition=None):
    session = MagicMock()
    mock_q = MagicMock()
    mock_q.join.return_value = mock_q
    mock_q.filter.return_value = mock_q
    mock_q.filter_by.return_value = mock_q

    if existing_edition:

        def first_side_effect():
            if not hasattr(first_side_effect, "calls"):
                first_side_effect.calls = 0
            val = existing_edition if first_side_effect.calls == 0 else None
            first_side_effect.calls += 1
            return val

        mock_q.first.side_effect = first_side_effect
    else:
        mock_q.first.return_value = None

    session.query.return_value = mock_q
    return session


@pytest.fixture
def mock_scout_manager():
    manager = MagicMock()
    manager.enrich.return_value = {
        "title": "Mock Title",
        "authors": ["Mock Author"],
        "isbn_13": "1234567890123",
        "genres": ["Fantasy"],
        "moods": ["Epic"],
    }
    return manager


@pytest.fixture
def mock_trope_manager():
    with patch("agentic_librarian.orchestration.assets.TropeManager") as mock:
        tm_inst = mock.return_value
        tm_inst.standardize_trope.return_value = MagicMock(id="mock-trope-id", name="Fantasy")
        yield tm_inst


@pytest.fixture
def mock_mlflow():
    with patch("agentic_librarian.orchestration.assets.mlflow") as mock:
        yield mock


@pytest.mark.slow
def test_enhance_job_integration_all_new(mock_scout_manager, mock_trope_manager, mock_mlflow):
    job_def = defs.get_job_def("enhance_job")
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions("csv_files", ["test_sample"])

    session = create_mock_session(existing_edition=None)
    mock_db_manager = MagicMock(spec=DatabaseManager)
    mock_db_manager.get_session.return_value.__enter__.return_value = session

    result = job_def.execute_in_process(
        partition_key="test_sample",
        resources={"db_manager": mock_db_manager, "scout_manager": mock_scout_manager},
        instance=instance,
    )

    assert result.success
    added_objects = [call[0][0] for call in session.add.call_args_list]
    history_entries = [obj for obj in added_objects if isinstance(obj, ReadingHistory)]
    assert len(history_entries) == 2
    assert mock_scout_manager.enrich.call_count == 2


@pytest.mark.slow
def test_enhance_job_deduplication(mock_scout_manager, mock_trope_manager, mock_mlflow):
    """Deduplication path: One book exists, one is new."""
    job_def = defs.get_job_def("enhance_job")
    instance = DagsterInstance.ephemeral()
    instance.add_dynamic_partitions("csv_files", ["test_sample"])

    existing_work = Work(title="The Way of Kings", id="existing-work-id")
    existing_edition = Edition(work=existing_work, format="hardcover", id="existing-edition-id")

    session = create_mock_session(existing_edition=existing_edition)
    mock_db_manager = MagicMock(spec=DatabaseManager)
    mock_db_manager.get_session.return_value.__enter__.return_value = session

    result = job_def.execute_in_process(
        partition_key="test_sample",
        resources={"db_manager": mock_db_manager, "scout_manager": mock_scout_manager},
        instance=instance,
    )

    assert result.success
    # Scout should only be called for the second row (Project Hail Mary)
    assert mock_scout_manager.enrich.call_count == 1
    added_objects = [call[0][0] for call in session.add.call_args_list]
    history_entries = [obj for obj in added_objects if isinstance(obj, ReadingHistory)]
    assert len(history_entries) == 2
