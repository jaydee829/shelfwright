import pytest
from uuid import UUID
from datetime import datetime
from src.agentic_librarian.db import models

def test_author_model_structure():
    """Verify Author model has expected columns."""
    assert hasattr(models.Author, "id")
    assert hasattr(models.Author, "name")
    assert hasattr(models.Author, "bio")
    assert hasattr(models.Author, "style_attributes")

def test_work_model_structure():
    """Verify Work model has expected columns."""
    assert hasattr(models.Work, "id")
    assert hasattr(models.Work, "title")
    assert hasattr(models.Work, "original_publication_year")
    assert hasattr(models.Work, "description")
    assert hasattr(models.Work, "genres")
    assert hasattr(models.Work, "moods")

def test_trope_model_structure():
    """Verify Trope model has expected columns."""
    assert hasattr(models.Trope, "id")
    assert hasattr(models.Trope, "name")
    assert hasattr(models.Trope, "description")
    assert hasattr(models.Trope, "embedding")

def test_edition_model_structure():
    """Verify Edition model has expected columns."""
    assert hasattr(models.Edition, "id")
    assert hasattr(models.Edition, "work_id")
    assert hasattr(models.Edition, "isbn_13")
    assert hasattr(models.Edition, "format")
    assert hasattr(models.Edition, "page_count")
    assert hasattr(models.Edition, "audio_minutes")
    assert hasattr(models.Edition, "publication_date")

def test_reading_history_model_structure():
    """Verify ReadingHistory model has expected columns."""
    assert hasattr(models.ReadingHistory, "id")
    assert hasattr(models.ReadingHistory, "edition_id")
    assert hasattr(models.ReadingHistory, "date_started")
    assert hasattr(models.ReadingHistory, "date_completed")
    assert hasattr(models.ReadingHistory, "user_rating")
    assert hasattr(models.ReadingHistory, "user_notes")

def test_suggestions_model_structure():
    """Verify Suggestions model has expected columns."""
    assert hasattr(models.Suggestions, "id")
    assert hasattr(models.Suggestions, "work_id")
    assert hasattr(models.Suggestions, "suggested_at")
    assert hasattr(models.Suggestions, "context")
    assert hasattr(models.Suggestions, "justification")
    assert hasattr(models.Suggestions, "status")
    assert hasattr(models.Suggestions, "conversation_id")
