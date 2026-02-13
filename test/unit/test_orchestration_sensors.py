import json
import os
from unittest.mock import MagicMock, patch

from agentic_librarian.orchestration.sensors import new_file_sensor
from dagster import SensorEvaluationContext


def test_new_file_sensor_no_files():
    context = MagicMock(spec=SensorEvaluationContext)
    context.cursor = None

    with patch("os.listdir", return_value=[]):
        results = list(new_file_sensor(context))
        assert len(results) == 0
        context.update_cursor.assert_not_called()


def test_new_file_sensor_with_files():
    context = MagicMock(spec=SensorEvaluationContext)
    context.cursor = None

    files = ["20251130.csv", "other.txt"]
    mtimes = {"20251130.csv": 1000.0, "other.txt": 1001.0}

    def mock_getmtime(path):
        return mtimes[os.path.basename(path)]

    def mock_isfile(path):
        return os.path.basename(path) in files

    with (
        patch("os.listdir", return_value=files),
        patch("os.path.isfile", side_effect=mock_isfile),
        patch("os.path.getmtime", side_effect=mock_getmtime),
    ):
        results = list(new_file_sensor(context))

        # Should only yield for .csv files
        assert len(results) == 1
        assert results[0].run_key == "20251130"
        assert results[0].partition_key == "20251130"

        # update_cursor should be called with the JSON cursor for the processed CSV
        expected_cursor = json.dumps({"last_mtime": 1000.0, "last_filename": "20251130.csv"})
        context.update_cursor.assert_called_once_with(expected_cursor)


def test_new_file_sensor_cursor_filtering():
    context = MagicMock(spec=SensorEvaluationContext)
    context.cursor = json.dumps({"last_mtime": 1000.0, "last_filename": "20251130.csv"})

    files = ["20251130.csv", "20251201.csv"]
    mtimes = {"20251130.csv": 1000.0, "20251201.csv": 1002.0}

    def mock_getmtime(path):
        return mtimes[os.path.basename(path)]

    def mock_isfile(path):
        return True

    with (
        patch("os.listdir", return_value=files),
        patch("os.path.isfile", side_effect=mock_isfile),
        patch("os.path.getmtime", side_effect=mock_getmtime),
    ):
        results = list(new_file_sensor(context))

        # Should only yield for files with (mtime, filename) > (1000.0, "20251130.csv")
        assert len(results) == 1
        assert results[0].run_key == "20251201"
        expected_cursor = json.dumps({"last_mtime": 1002.0, "last_filename": "20251201.csv"})
        context.update_cursor.assert_called_once_with(expected_cursor)


def test_new_file_sensor_identical_timestamps():
    """Verify that multiple files with the same timestamp are processed in order and not skipped."""
    context = MagicMock(spec=SensorEvaluationContext)
    context.cursor = None

    # Use 6 files to trigger batching (batch_size is 5 in the code)
    files = ["f1.csv", "f2.csv", "f3.csv", "f4.csv", "f5.csv", "f6.csv"]
    mtime = 1000.0

    with (
        patch("os.listdir", return_value=files),
        patch("os.path.isfile", return_value=True),
        patch("os.path.getmtime", return_value=mtime),
    ):
        # Run 1: Should get f1 to f5
        results = list(new_file_sensor(context))
        assert len(results) == 5
        assert results[0].run_key == "f1"
        assert results[4].run_key == "f5"

        expected_cursor = json.dumps({"last_mtime": mtime, "last_filename": "f5.csv"})
        context.update_cursor.assert_called_once_with(expected_cursor)

        # Run 2: Setup context with the new cursor
        context.cursor = expected_cursor
        context.update_cursor.reset_mock()

        results = list(new_file_sensor(context))
        assert len(results) == 1
        assert results[0].run_key == "f6"

        final_cursor = json.dumps({"last_mtime": mtime, "last_filename": "f6.csv"})
        context.update_cursor.assert_called_once_with(final_cursor)


def test_new_file_sensor_migration():
    """Verify that the sensor can migrate from an old numeric cursor."""
    context = MagicMock(spec=SensorEvaluationContext)
    context.cursor = "1000.0"

    files = ["20251201.csv"]

    with (
        patch("os.listdir", return_value=files),
        patch("os.path.isfile", return_value=True),
        patch("os.path.getmtime", return_value=1002.0),
    ):
        results = list(new_file_sensor(context))
        assert len(results) == 1
        assert results[0].run_key == "20251201"
        expected_cursor = json.dumps({"last_mtime": 1002.0, "last_filename": "20251201.csv"})
        context.update_cursor.assert_called_once_with(expected_cursor)
