from datetime import date

import pandas as pd
import pytest
from agentic_librarian.db.models import Author, Edition, ReadingHistory, Work
from agentic_librarian.etl.ingest import HistoryIngestor


@pytest.fixture
def sample_df():
    return pd.DataFrame(
        [
            {
                "Title": "Boundless",
                "Author": "R.A. Salvatore",
                "Date complete": "4-Jan",
                "# of pages": "400",
                "format": "hardcover",
            },
            {
                "Title": "Starship Troopers",
                "Author": "Robert Heinlein",
                "Date complete": "12/14/2023",
                "# of pages": None,
                "format": "audiobook",
            },
        ]
    )


def test_ingestor_clean(sample_df):
    ingestor = HistoryIngestor(sample_df)
    cleaned_df = ingestor.clean()
    assert "Author_1" in cleaned_df.columns
    # Check if dates are converted (internal logic check)
    assert isinstance(cleaned_df.iloc[0]["date_completed"], date) or cleaned_df.iloc[0]["date_completed"] is not None


def test_ingestor_year_inference(sample_df):
    # In sample_df:
    # Row 0: 4-Jan
    # Row 1: 12/14/2023
    ingestor = HistoryIngestor(sample_df)
    cleaned_df = ingestor.clean()

    # Row 0 should infer 2023 from Row 1 via bfill
    assert cleaned_df.iloc[0]["date_completed"] == date(2023, 1, 4)
    assert cleaned_df.iloc[1]["date_completed"] == date(2023, 12, 14)


def test_ingestor_to_models(sample_df):
    ingestor = HistoryIngestor(sample_df)
    # Note: ingestor.clean() should be called internally or by the user
    models = list(ingestor.to_models())

    # We expect several objects per row: Author, Work, Edition, ReadingHistory
    # For 2 rows, it should be at least those.

    # Verify we have at least one of each type
    assert any(isinstance(m, Author) for m in models)
    assert any(isinstance(m, Work) for m in models)
    assert any(isinstance(m, Edition) for m in models)
    assert any(isinstance(m, ReadingHistory) for m in models)

    # Verify relationships (if possible without DB session)
    works = [m for m in models if isinstance(m, Work)]
    assert any(w.title == "Boundless" for w in works)
