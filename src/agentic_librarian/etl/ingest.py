import re
from collections.abc import Generator

import pandas as pd
from agentic_librarian.db.models import Author, Base, Edition, ReadingHistory, Work
from agentic_librarian.etl import cleaning


class HistoryIngestor:
    """Orchestrates CSV ingestion and mapping to database models."""

    def __init__(self, df: pd.DataFrame):
        self.raw_df = df
        self.cleaned_df = None

    def clean(self) -> pd.DataFrame:
        """Apply all cleaning transformations to the raw data."""
        df = self.raw_df.copy()

        # Rename columns to standardized internal names if needed
        # Expected CSV: Title,Author,Date complete,# of pages,format
        column_map = {"Date complete": "date_completed", "# of pages": "page_count", "format": "format"}
        df = df.rename(columns=column_map)

        # Apply cleaning utilities
        df = cleaning.split_formats(df)
        df = cleaning.split_authors(df)

        # Parse dates with context (infer year from previous/next rows)
        if "date_completed" in df.columns:
            # First, try to extract years from unambiguous dates
            def get_year(s):
                if not s or pd.isna(s):
                    return None
                try:
                    parsed = pd.to_datetime(s, errors="coerce")
                    # If it's unambiguous (not 1900 and not current year by default from a short string)
                    # Actually pd.to_datetime("4-Jan") gives 2026-01-04 today.
                    # We need to check if the original string has an explicit 4-digit year.
                    if re.search(r"\b\d{4}\b", str(s)):
                        return parsed.year if not pd.isna(parsed) else None
                    return None
                except (ValueError, TypeError):
                    return None

            years = df["date_completed"].apply(get_year)
            years = years.ffill().bfill()  # Contextual inference

            # Now parse with fallback years
            parsed_dates = []
            for i, val in enumerate(df["date_completed"]):
                fallback = int(years.iloc[i]) if not pd.isna(years.iloc[i]) else None
                parsed_dates.append(cleaning.parse_completion_date(val, fallback_year=fallback))
            df["date_completed"] = parsed_dates

        # Convert numeric columns
        if "page_count" in df.columns:
            df["page_count"] = pd.to_numeric(df["page_count"], errors="coerce").fillna(0).astype(int)

        self.cleaned_df = df
        return df

    def to_models(self) -> Generator[Base, None, None]:
        """Generator yielding SQLAlchemy model instances from cleaned data."""
        if self.cleaned_df is None:
            self.clean()

        for _, row in self.cleaned_df.iterrows():
            # 1. Author(s)
            authors = []
            author_cols = [c for c in self.cleaned_df.columns if c.startswith("Author_")]
            for col in author_cols:
                name = row[col]
                if name and not pd.isna(name):
                    authors.append(Author(name=name))

            # 2. Work
            work = Work(title=row["Title"], authors=authors)

            # 3. Edition
            edition = Edition(
                work=work,
                format=row["format"],
                page_count=int(row["page_count"]) if row["page_count"] > 0 else None,
            )

            # 4. Reading History
            if row["date_completed"] and not pd.isna(row["date_completed"]):
                reading_history = ReadingHistory(edition=edition, date_completed=row["date_completed"])
                yield reading_history

            # Since relationships are defined, yielding reading_history
            # should pull in the rest if we are using logic that traverses them.
            # However, for explicit creation, we yield everything or rely on cascades.
            # The plan says "map rows into internal objects".

            yield work
            yield from authors
            yield edition
