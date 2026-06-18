import pytest

from agentic_librarian.db import models


@pytest.mark.parametrize(
    "model_class,attribute",
    [
        (models.Author, "id"),
        (models.Author, "name"),
        (models.Author, "bio"),
        (models.Author, "styles"),
        (models.Work, "id"),
        (models.Work, "title"),
        (models.Work, "original_publication_year"),
        (models.Work, "description"),
        (models.Work, "genres"),
        (models.Work, "moods"),
        (models.Trope, "id"),
        (models.Trope, "name"),
        (models.Trope, "description"),
        (models.Trope, "embedding"),
        (models.WorkTrope, "work_id"),
        (models.WorkTrope, "trope_id"),
        (models.WorkTrope, "relevance_score"),
        (models.WorkTrope, "justification"),
        (models.WorkContributor, "work_id"),
        (models.WorkContributor, "author_id"),
        (models.WorkContributor, "role"),
        (models.Style, "id"),
        (models.Style, "name"),
        (models.Style, "category"),
        (models.Style, "embedding"),
        (models.AuthorStyle, "author_id"),
        (models.AuthorStyle, "style_id"),
        (models.AuthorStyle, "attribute_type"),
        (models.NarratorStyle, "narrator_id"),
        (models.NarratorStyle, "style_id"),
        (models.NarratorStyle, "attribute_type"),
        (models.Edition, "id"),
        (models.Edition, "work_id"),
        (models.Edition, "isbn_13"),
        (models.Edition, "format"),
        (models.Edition, "page_count"),
        (models.Edition, "audio_minutes"),
        (models.Edition, "publication_date"),
        (models.ReadingHistory, "id"),
        (models.ReadingHistory, "edition_id"),
        (models.ReadingHistory, "date_started"),
        (models.ReadingHistory, "date_completed"),
        (models.ReadingHistory, "user_rating"),
        (models.ReadingHistory, "user_notes"),
        (models.Suggestions, "id"),
        (models.Suggestions, "work_id"),
        (models.Suggestions, "suggested_at"),
        (models.Suggestions, "context"),
        (models.Suggestions, "justification"),
        (models.Suggestions, "status"),
        (models.Suggestions, "conversation_id"),
    ],
)
def test_model_structure_parameterized(model_class, attribute):
    """Verify that all core models have their expected columns."""
    assert hasattr(model_class, attribute)


def test_import_job_and_row_models_exist():
    from agentic_librarian.db.models import ImportJob, ImportRow

    assert ImportJob.__tablename__ == "import_jobs"
    assert ImportRow.__tablename__ == "import_rows"
    # ImportRow carries everything the worker needs without loading the job.
    cols = ImportRow.__table__.columns.keys()
    for c in ("import_job_id", "user_id", "raw_title", "raw_author", "raw_format",
              "raw_date", "date_completed", "rating", "notes", "destination",
              "shelf", "status", "outcome", "skip_reason", "work_id", "error_detail"):
        assert c in cols, c
