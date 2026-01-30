import os
from unittest.mock import MagicMock, patch
from dagster import SensorEvaluationContext
from agentic_librarian.orchestration.sensors import new_file_sensor

def test_new_file_sensor_no_files():
    context = MagicMock(spec=SensorEvaluationContext)
    context.cursor = None
    
    with patch("os.listdir", return_value=[]):
        results = list(new_file_sensor(context))
        assert len(results) == 0
        context.update_cursor.assert_called_once_with("0")

def test_new_file_sensor_with_files():
    context = MagicMock(spec=SensorEvaluationContext)
    context.cursor = None
    
    files = ["20251130.csv", "other.txt"]
    mtimes = {
        "20251130.csv": 1000.0,
        "other.txt": 1001.0
    }
    
    def mock_getmtime(path):
        return mtimes[os.path.basename(path)]

    def mock_isfile(path):
        return os.path.basename(path) in files

    with patch("os.listdir", return_value=files), \
         patch("os.path.isfile", side_effect=mock_isfile), \
         patch("os.path.getmtime", side_effect=mock_getmtime):
        
        results = list(new_file_sensor(context))
        
        # Should only yield for .csv files
        assert len(results) == 1
        assert results[0].run_key == "20251130"
        assert results[0].partition_key == "20251130"
        
        # update_cursor should be called with the max mtime of processed CSVs
        context.update_cursor.assert_called_once_with("1000.0")

def test_new_file_sensor_cursor_filtering():
    context = MagicMock(spec=SensorEvaluationContext)
    context.cursor = "1000.0"
    
    files = ["20251130.csv", "20251201.csv"]
    mtimes = {
        "20251130.csv": 1000.0,
        "20251201.csv": 1002.0
    }
    
    def mock_getmtime(path):
        return mtimes[os.path.basename(path)]

    def mock_isfile(path):
        return True

    with patch("os.listdir", return_value=files), \
         patch("os.path.isfile", side_effect=mock_isfile), \
         patch("os.path.getmtime", side_effect=mock_getmtime):
        
        results = list(new_file_sensor(context))
        
        # Should only yield for files with mtime > 1000.0
        assert len(results) == 1
        assert results[0].run_key == "20251201"
        context.update_cursor.assert_called_once_with("1002.0")
