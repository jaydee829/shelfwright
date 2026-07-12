import os

from sqlalchemy.orm import Session

from agentic_librarian.db.models import Style
from agentic_librarian.scouts.utils import get_cached_embedding


class StyleManager:
    """Manages style seeding, vectorization, and similarity deduplication for Authors and Narrators."""

    def __init__(self, session: Session, api_key: str = None):
        self.session = session
        self._api_key = api_key or os.environ.get("GOOGLE_SEARCH_API_KEY")
        if not self._api_key:
            raise ValueError("Google API key not set for StyleManager.")
        self.model_name = "gemini-embedding-001"  # Current GA Gemini embedding model

    def _get_embedding(self, text: str) -> list[float]:
        """Fetch embedding from Gemini via the shared module-level client + cache (#101)."""
        return get_cached_embedding(self.model_name, text)

    def find_similar_style(self, embedding: list[float], category: str, threshold: float = 0.85) -> Style | None:
        """Find an existing style in the same category with cosine similarity above threshold using SQL-level search."""
        # pgvector cosine_distance is (1 - cosine_similarity)
        # So similarity >= 0.85 is distance <= 0.15
        max_distance = 1.0 - threshold

        # Using pgvector's cosine_distance operator directly in SQL
        similar_style = (
            self.session.query(Style)
            .filter(Style.category == category)
            .filter(Style.embedding.cosine_distance(embedding) <= max_distance)
            .order_by(Style.embedding.cosine_distance(embedding))
            .first()
        )

        return similar_style

    def standardize_style(self, raw_tag: str, category: str, threshold: float = 0.85) -> Style:
        """
        Maps a raw style tag to a standardized Style entity.
        Checks for exact name match first, then semantic similarity within the category.
        """
        # 1. Exact Name Match
        existing = self.session.query(Style).filter(Style.name == raw_tag, Style.category == category).first()
        if existing:
            return existing

        # 2. Semantic Match
        embedding = self._get_embedding(raw_tag)
        similar = self.find_similar_style(embedding, category=category, threshold=threshold)

        if similar:
            return similar

        # 3. Create New
        new_style = Style(name=raw_tag, category=category, embedding=embedding)
        self.session.add(new_style)
        self.session.flush()
        return new_style
